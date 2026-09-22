"""The /backup/v1 client against a fake admin + R2 (one aiohttp test server)."""

from __future__ import annotations

import json

import aiohttp
import pytest
from aiohttp import web

from hlb_integration.api import (
    ApiAuthError,
    ApiConnectionError,
    ApiDisabledError,
    ApiError,
    ApiNotFoundError,
    ApiUnavailableError,
    HomeLabsBackupApi,
)

TOKEN = "hls_test"


@pytest.fixture
async def server(aiohttp_server):
    seen: list[tuple[str, str, str | None]] = []
    state = {"status": 200, "body": {}}

    async def record(request: web.Request) -> None:
        seen.append((request.method, request.path, request.headers.get("Authorization")))

    async def admin(request: web.Request) -> web.Response:
        await record(request)
        return web.json_response(state["body"], status=state["status"])

    async def r2_put(request: web.Request) -> web.Response:
        await record(request)
        await request.read()
        return web.Response(headers={"ETag": '"abc"'})

    async def r2_get(request: web.Request) -> web.StreamResponse:
        await record(request)
        return web.Response(body=b"x" * 3_000_000)

    app = web.Application()
    app.router.add_route("*", "/backup/v1/{tail:.*}", admin)
    app.router.add_put("/r2/{key}", r2_put)
    app.router.add_get("/r2/{key}", r2_get)
    srv = await aiohttp_server(app)
    srv.seen = seen
    srv.state = state
    return srv


@pytest.fixture
async def api(server):
    async with aiohttp.ClientSession() as session:
        yield HomeLabsBackupApi(session, str(server.make_url("/")), TOKEN, "test/1")


async def test_admin_calls_carry_the_token(api, server):
    server.state["body"] = {"backups": [{"backup_id": "a"}, "junk"]}
    assert await api.list_backups() == [{"backup_id": "a"}]
    assert server.seen == [("GET", "/backup/v1/backups", f"Bearer {TOKEN}")]


async def test_r2_calls_never_carry_the_token(api, server):
    url = str(server.make_url("/r2/part"))
    assert await api.put_part(url, b"data") == '"abc"'
    chunks = [c async for c in await api.iter_download(str(server.make_url("/r2/tar")))]
    assert sum(len(c) for c in chunks) == 3_000_000
    assert [auth for _, _, auth in server.seen] == [None, None]


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, ApiAuthError),
        (403, ApiDisabledError),
        (404, ApiNotFoundError),
        (503, ApiUnavailableError),
        (413, ApiError),
    ],
)
async def test_errors_map_to_types_with_the_servers_message(api, server, status, exc):
    server.state.update(status=status, body={"detail": "Brak miejsca"})
    with pytest.raises(exc, match="Brak miejsca") as info:
        await api.start_upload({"backup_id": "a"})
    assert info.value.status == status


async def test_complete_and_parts_payloads(api, server):
    server.state["body"] = {"urls": {"1": "u1", "2": "u2"}}
    assert await api.part_urls("up", [1, 2]) == {1: "u1", 2: "u2"}
    server.state["body"] = {"backup_id": "a", "size": 3}
    assert (await api.complete("up", [(1, "e1")]))["size"] == 3
    assert server.seen[-1][:2] == ("POST", "/backup/v1/uploads/up/complete")


async def test_unreachable_server_is_a_connection_error():
    async with aiohttp.ClientSession() as session:
        api = HomeLabsBackupApi(session, "http://127.0.0.1:9", TOKEN, "t")
        with pytest.raises(ApiConnectionError):
            await api.status()


def test_admin_error_body_can_be_a_validation_list():
    err = HomeLabsBackupApi._error(422, json.dumps({"detail": [{"msg": "bad"}]}))
    assert "bad" in str(err) and err.status == 422
