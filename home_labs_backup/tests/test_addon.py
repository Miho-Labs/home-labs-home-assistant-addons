"""The add-on process: installer, one pass against fake admin + Supervisor, versions."""

from __future__ import annotations

import json
import re
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from hl_backup import VERSION, installer
from hl_backup.__main__ import NOTIFY_RESTART, NOTIFY_TOKEN, run_once
from hl_backup.options import Options, Paths, load_options
from hl_backup.supervisor import SupervisorApi

ADDON = Path(__file__).resolve().parent.parent
INTEGRATION = ADDON / "custom_components" / "home_labs_backup"


# ----- installer


def test_install_update_and_unchanged(tmp_path):
    dst = tmp_path / "config" / "custom_components" / "home_labs_backup"
    assert installer.install(INTEGRATION, dst) == "installed"
    assert (dst / "manifest.json").read_bytes() == (INTEGRATION / "manifest.json").read_bytes()
    assert installer.install(INTEGRATION, dst) == "unchanged"

    (dst / "const.py").write_text("# edited by hand\n")
    (dst / "stale.py").write_text("x = 1\n")
    assert installer.install(INTEGRATION, dst) == "updated"
    assert not (dst / "stale.py").exists()
    assert installer.tree_digest(dst) == installer.tree_digest(INTEGRATION)
    # No staging or outgoing copies left next to it.
    assert sorted(p.name for p in dst.parent.iterdir()) == ["home_labs_backup"]


def test_pycache_does_not_count_as_a_change(tmp_path):
    dst = tmp_path / "home_labs_backup"
    installer.install(INTEGRATION, dst)
    (dst / "__pycache__").mkdir()
    (dst / "__pycache__" / "api.cpython-313.pyc").write_bytes(b"\0")
    assert installer.install(INTEGRATION, dst) == "unchanged"


def test_missing_source_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        installer.install(tmp_path / "nope", tmp_path / "dst")


# ----- options


def test_options_defaults_and_cleanup(tmp_path):
    path = tmp_path / "options.json"
    assert load_options(path).api_url == "https://public.home-labs.pl"
    path.write_text(
        json.dumps({"api_url": "https://x/ ", "api_token": " hls_a ", "log_level": "?"})
    )
    opts = load_options(path)
    assert (opts.api_url, opts.api_token, opts.log_level) == ("https://x", "hls_a", "info")


# ----- one pass


@pytest.fixture
async def fake(aiohttp_server):
    calls: list[tuple[str, str, dict | None]] = []
    state = {"status": 200, "body": {"enabled": True, "client": "dom", "backups": 2}}

    async def status(request: web.Request) -> web.Response:
        calls.append(("admin", request.headers.get("Authorization", ""), None))
        return web.json_response(state["body"], status=state["status"])

    async def supervisor(request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else None
        calls.append((request.method + " " + request.path, request.headers["Authorization"], body))
        if request.path == "/discovery":
            return web.json_response({"result": "ok", "data": {"uuid": "d-1"}})
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/backup/v1/status", status)
    app.router.add_route("*", "/discovery", supervisor)
    app.router.add_route("*", "/core/api/services/{tail:.*}", supervisor)
    srv = await aiohttp_server(app)
    srv.calls = calls
    srv.state = state
    return srv


async def _pass(fake, tmp_path, token="hls_ok"):
    base = str(fake.make_url("")).rstrip("/")
    paths = Paths(
        config_dir=tmp_path / "config", data_dir=tmp_path / "data", integration_src=INTEGRATION
    )
    async with aiohttp.ClientSession() as session:
        sup = SupervisorApi(session, base, "sup-token")
        return await run_once(Options(api_url=base, api_token=token), paths, session, sup), paths


async def test_first_pass_installs_announces_and_asks_for_a_restart(fake, tmp_path):
    result, paths = await _pass(fake, tmp_path)
    assert result == {"token": "ok", "install": "installed", "discovery": "sent"}
    assert (paths.integration_dst / "manifest.json").is_file()
    discovery = [c for c in fake.calls if c[0] == "POST /discovery"]
    assert discovery[0][1] == "Bearer sup-token"
    assert discovery[0][2] == {
        "service": "home_labs_backup",
        "config": {"api_url": str(fake.make_url("")).rstrip("/"), "api_token": "hls_ok"},
    }
    created = [c[2]["notification_id"] for c in fake.calls if c[0].endswith("/create")]
    assert created == [NOTIFY_RESTART]
    assert ("admin", "Bearer hls_ok", None) in fake.calls


async def test_second_pass_changes_nothing_but_still_announces(fake, tmp_path):
    await _pass(fake, tmp_path)
    fake.calls.clear()
    result, _ = await _pass(fake, tmp_path)
    assert result["install"] == "unchanged"
    assert not [c for c in fake.calls if c[0].endswith("/create")]
    assert [c for c in fake.calls if c[0] == "POST /discovery"]


async def test_rejected_token_notifies_but_installs(fake, tmp_path):
    fake.state.update(status=401, body={"detail": "Nieprawidłowy token"})
    result, _ = await _pass(fake, tmp_path)
    assert result["token"] == "auth" and result["install"] == "installed"
    created = [c[2]["notification_id"] for c in fake.calls if c[0].endswith("/create")]
    assert NOTIFY_TOKEN in created


async def test_backups_switched_off_is_only_a_warning(fake, tmp_path):
    fake.state["body"] = {"enabled": False, "client": "dom"}
    result, _ = await _pass(fake, tmp_path)
    assert result["token"] == "disabled"
    assert [c for c in fake.calls if c[0].endswith("/dismiss")]


async def test_no_token_does_nothing_but_ask_for_one(fake, tmp_path):
    result, paths = await _pass(fake, tmp_path, token="")
    assert result == {"token": "missing", "install": None, "discovery": None}
    assert not paths.integration_dst.exists()
    created = [c[2]["notification_id"] for c in fake.calls if c[0].endswith("/create")]
    assert created == [NOTIFY_TOKEN]


# ----- versions and translations


def test_versions_agree():
    from hlb_integration import const

    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    assert manifest["version"] == const.VERSION
    config = (ADDON / "config.yaml").read_text()
    assert re.search(r"^version: (\S+)$", config, re.M).group(1) == VERSION


def test_brand_images_ship_with_the_integration(tmp_path):
    # HA (2026.3+) serves a custom integration's icons from its own brand/ folder;
    # without them the backup location shows "icon not available".
    sizes = {"icon.png": (256, 256), "icon@2x.png": (512, 512), "logo.png": (320, 128)}
    for name, size in sizes.items():
        head = (INTEGRATION / "brand" / name).read_bytes()[:24]
        assert head[:8] == b"\x89PNG\r\n\x1a\n", name
        assert (int.from_bytes(head[16:20]), int.from_bytes(head[20:24])) == size, name
    dst = tmp_path / "home_labs_backup"
    installer.install(INTEGRATION, dst)
    assert (dst / "brand" / "icon.png").is_file()


def test_translations_have_the_same_keys():
    def keys(obj, prefix=""):
        if isinstance(obj, dict):
            return {k2 for k, v in obj.items() for k2 in keys(v, f"{prefix}{k}.")}
        return {prefix}

    strings = json.loads((INTEGRATION / "strings.json").read_text())
    for lang in ("en", "pl"):
        data = json.loads((INTEGRATION / "translations" / f"{lang}.json").read_text())
        assert keys(data) == keys(strings), lang
