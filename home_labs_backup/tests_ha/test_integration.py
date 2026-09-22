"""The integration inside Home Assistant: config flows, setup, the backup agent."""

from __future__ import annotations

import json
from http import HTTPStatus

import pytest
from homeassistant import config_entries
from homeassistant.components.backup import AgentBackup, BackupAgentError, BackupNotFound
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.home_labs_backup import backup as backup_platform
from custom_components.home_labs_backup import uploader
from custom_components.home_labs_backup.const import CONF_API_TOKEN, CONF_API_URL, DOMAIN, TITLE

API = "https://admin.test"
R2 = "https://r2.test"
TOKEN = "hls_token"

STATUS = {
    "enabled": True,
    "client": "dom",
    "quota_bytes": 20 * 1024**3,
    "max_backup_bytes": 10 * 1024**3,
    "part_size": 4,
    "used_bytes": 0,
    "backups": 0,
    "require_protected": True,
}


def _backup(backup_id="abc123", protected=True, size=10) -> AgentBackup:
    return AgentBackup(
        addons=[],
        backup_id=backup_id,
        date="2026-09-22T03:00:00+02:00",
        database_included=True,
        extra_metadata={"with_automatic_settings": True},
        folders=[],
        homeassistant_included=True,
        homeassistant_version="2026.2.3",
        name="Automatyczna kopia",
        protected=protected,
        size=size,
    )


def _discovery(token=TOKEN) -> HassioServiceInfo:
    return HassioServiceInfo(
        config={CONF_API_URL: API + "/", CONF_API_TOKEN: token},
        name="Home Labs Backup",
        slug="home_labs_backup",
        uuid="1234",
    )


# ----- config flow


async def test_discovery_creates_the_entry_without_a_click(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.get(f"{API}/backup/v1/status", json=STATUS)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=_discovery()
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == {CONF_API_URL: API, CONF_API_TOKEN: TOKEN}


async def test_a_new_token_from_the_addon_updates_the_entry(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.get(f"{API}/backup/v1/status", json=STATUS)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, data={CONF_API_URL: API, CONF_API_TOKEN: "hls_old"}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=_discovery("hls_new")
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert entry.data[CONF_API_TOKEN] == "hls_new"


async def test_discovery_without_a_token_aborts(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=_discovery("")
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "missing_token"


async def test_manual_setup_checks_the_token(hass: HomeAssistant, aioclient_mock):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    aioclient_mock.get(f"{API}/backup/v1/status", status=401, json={"detail": "zły"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_URL: API, CONF_API_TOKEN: "hls_bad"}
    )
    assert result["errors"] == {"base": "invalid_token"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/backup/v1/status", json=STATUS)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_URL: API + "/", CONF_API_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_URL] == API


# ----- setup


async def _setup(hass: HomeAssistant, aioclient_mock, status=HTTPStatus.OK) -> MockConfigEntry:
    aioclient_mock.get(f"{API}/backup/v1/status", json=STATUS, status=status)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, data={CONF_API_URL: API, CONF_API_TOKEN: TOKEN}
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "backup", {})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_bad_token_fails_setup(hass: HomeAssistant, aioclient_mock):
    entry = await _setup(hass, aioclient_mock, status=HTTPStatus.UNAUTHORIZED)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_unreachable_server_retries(hass: HomeAssistant, aioclient_mock):
    entry = await _setup(hass, aioclient_mock, status=HTTPStatus.SERVICE_UNAVAILABLE)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_the_agent_is_registered(hass: HomeAssistant, aioclient_mock):
    entry = await _setup(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED
    agents = await backup_platform.async_get_backup_agents(hass)
    assert [(a.agent_id, a.name) for a in agents] == [(f"{DOMAIN}.{entry.entry_id}", TITLE)]
    manager = hass.data["backup"]
    assert f"{DOMAIN}.{entry.entry_id}" in manager.backup_agents


# ----- the agent


@pytest.fixture
async def agent(hass: HomeAssistant, aioclient_mock):
    await _setup(hass, aioclient_mock)
    (agent,) = await backup_platform.async_get_backup_agents(hass)
    return agent


async def test_upload_goes_to_r2_without_the_token(hass, aioclient_mock, agent, monkeypatch):
    monkeypatch.setattr(uploader, "RETRY_DELAY", 0)
    put_bodies: dict[str, bytes] = {}

    async def put_part(method, url, data):
        put_bodies[str(url)] = bytes(data)
        return AiohttpClientMockResponse(method, url, headers={"ETag": f'"e{len(put_bodies)}"'})

    aioclient_mock.post(
        f"{API}/backup/v1/uploads", json={"upload_id": "u1", "part_size": 4, "max_parts": 100}
    )
    aioclient_mock.post(
        f"{API}/backup/v1/uploads/u1/parts",
        json={"urls": {str(n): f"{R2}/part{n}" for n in range(1, 11)}},
    )
    for n in range(1, 4):
        aioclient_mock.put(f"{R2}/part{n}", side_effect=put_part)
    aioclient_mock.post(f"{API}/backup/v1/uploads/u1/complete", json={"backup_id": "abc123"})

    async def stream():
        yield b"0123456"
        yield b"789"

    progress: list[int] = []
    await agent.async_upload_backup(
        open_stream=lambda: _coro(stream()),
        backup=_backup(),
        on_progress=lambda *, bytes_uploaded, **kw: progress.append(bytes_uploaded),
    )
    assert list(put_bodies.values()) == [b"0123", b"4567", b"89"]
    assert progress == [4, 8, 10]

    calls = {(m.upper(), str(u)): (d, h) for m, u, d, h in aioclient_mock.mock_calls}
    start_body, start_headers = calls[("POST", f"{API}/backup/v1/uploads")]
    assert _json(start_body)["backup_id"] == "abc123"
    assert start_headers["Authorization"] == f"Bearer {TOKEN}"
    for n in range(1, 4):
        _, headers = calls[("PUT", f"{R2}/part{n}")]
        assert "Authorization" not in (headers or {})
    complete_body, _ = calls[("POST", f"{API}/backup/v1/uploads/u1/complete")]
    assert _json(complete_body)["parts"] == [
        {"part_number": 1, "etag": '"e1"'},
        {"part_number": 2, "etag": '"e2"'},
        {"part_number": 3, "etag": '"e3"'},
    ]


async def test_unencrypted_backup_is_refused_before_any_request(hass, aioclient_mock, agent):
    aioclient_mock.clear_requests()
    with pytest.raises(BackupAgentError, match="szyfrowane"):
        await agent.async_upload_backup(
            open_stream=lambda: _coro(_empty()), backup=_backup(protected=False), on_progress=None
        )
    assert aioclient_mock.call_count == 0


async def test_switched_off_is_a_readable_error(hass, aioclient_mock, agent):
    aioclient_mock.post(
        f"{API}/backup/v1/uploads", status=403, json={"detail": "wyłączone dla tego domu"}
    )
    with pytest.raises(BackupAgentError, match="wyłączone dla tego domu"):
        await agent.async_upload_backup(
            open_stream=lambda: _coro(_empty()), backup=_backup(), on_progress=None
        )


async def test_list_get_download_delete(hass, aioclient_mock, agent):
    meta = _backup().as_dict()
    aioclient_mock.get(f"{API}/backup/v1/backups", json={"backups": [meta, {"broken": 1}]})
    listed = await agent.async_list_backups()
    assert [b.backup_id for b in listed] == ["abc123"]
    assert (await agent.async_get_backup("abc123")).name == "Automatyczna kopia"
    with pytest.raises(BackupNotFound):
        await agent.async_get_backup("nope")

    aioclient_mock.get(
        f"{API}/backup/v1/backups/abc123/download", json={"url": f"{R2}/tar", "expires_in": 3600}
    )
    aioclient_mock.get(f"{R2}/tar", content=b"tar-bytes")
    chunks = [c async for c in await agent.async_download_backup("abc123")]
    assert b"".join(chunks) == b"tar-bytes"

    aioclient_mock.delete(f"{API}/backup/v1/backups/abc123", status=204)
    await agent.async_delete_backup("abc123")
    with pytest.raises(BackupNotFound):
        await agent.async_delete_backup("nope")


def _json(body):
    # The mocker records `json=` bodies as the object itself.
    return body if isinstance(body, dict) else json.loads(body)


async def _coro(value):
    return value


async def _empty():
    return
    yield
