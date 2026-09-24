"""Shared fixtures: fake admin server, fake Supervisor and a runner factory."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest_asyncio
from aiohttp import web

from hl_sync.client import AdminClient
from hl_sync.hass import HassApi
from hl_sync.manifest import scope_for_path
from hl_sync.options import Options, Paths
from hl_sync.sync import SyncRunner

TOKEN = "hls_" + "A" * 43
COMMIT = "a" * 40
RELEASE_ID = "11111111-1111-1111-1111-111111111111"
HEADER = "entity_id;name;area;floor;device_class;unit;state"

DEFAULT_CONFIGURATION = """# Loads default set of integrations. Do not remove.
default_config:

# Load frontend themes from the themes folder
frontend:
  themes: !include_dir_merge_named themes

automation: !include automations.yaml
script: !include scripts.yaml
scene: !include scenes.yaml
"""

LOVELACE_FRAGMENT = (
    "lovelace:\n"
    "  dashboards:\n"
    "    panel-dom:\n"
    "      mode: yaml\n"
    "      filename: dashboards/dom.yaml\n"
    "      title: Dom\n"
    "      icon: mdi:home-heart\n"
    "      show_in_sidebar: true\n"
)

SAMPLE_FILES: dict[str, bytes] = {
    "dashboards/dom.yaml": b"title: Dom\nviews: []\n",
    "dashboards/common/badges.yaml": b"- type: entity\n  entity: sun.sun\n",
    "www/dashboard/tlo.jpg": bytes(range(256)) * 4,
    "themes/homelabs.yaml": b"Home Labs Noc:\n  primary-color: '#4fb3ff'\n",
    "home_labs/automations.yaml": (
        b"- id: '1789848132433'\n  alias: HL test\n  triggers: []\n  actions: []\n"
    ),
    "home_labs/scenes.yaml": b"- id: homelabs_kule_baltyk\n  name: Kule Baltyk\n  entities: {}\n",
}
MANAGED = {"automations": ["1789848132433"], "scenes": ["homelabs_kule_baltyk"]}

# WallPanel screensaver photos: root ``media``, land in <media_dir>/wallpanel/.
MEDIA_FILES: dict[str, bytes] = {
    "wallpanel/las.jpg": b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8,
    "wallpanel/morze.jpg": b"\xff\xd8\xff\xe0" + bytes(reversed(range(256))) * 8,
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_release(
    files: dict[str, bytes],
    *,
    media: dict[str, bytes] | None = None,
    lovelace: str | None = LOVELACE_FRAGMENT,
    packages: str | None = None,
    managed: dict | None = None,
    templates: dict | None = None,
    release_id: str = RELEASE_ID,
    commit: str = COMMIT,
    warnings: list | None = None,
) -> dict:
    """``files`` are config-root entries, ``media`` are media-root entries (scope ``media``)."""
    entries = [
        {
            "path": path,
            "root": "config",
            "scope": scope_for_path(path),
            "sha256": sha(data),
            "size": len(data),
        }
        for path, data in files.items()
    ]
    entries += [
        {"path": path, "root": "media", "scope": "media", "sha256": sha(data), "size": len(data)}
        for path, data in (media or {}).items()
    ]
    package: dict[str, str] = {}
    if lovelace is not None:
        package["lovelace"] = lovelace
    if packages is not None:
        package["packages"] = packages
    return {
        "release_id": release_id,
        "commit": commit,
        "published_at": "2026-09-22T10:00:00+00:00",
        "files": entries,
        "package": package,
        "managed_ids": managed or {"automations": [], "scenes": []},
        "templates": templates or {},
        "warnings": warnings or [],
    }


def base_manifest(release: dict | None = None, **fields) -> dict:
    manifest = {
        "schema": 1,
        "client": "testowy",
        "paused": False,
        "release": release,
        "requests": {"entities_export": None},
    }
    manifest.update(fields)
    return manifest


class FakeAdmin:
    def __init__(self) -> None:
        self.token = TOKEN
        self.manifest = base_manifest()
        self._etag_counter = 0
        self.etag = self._new_etag()
        self.blobs: dict[str, bytes] = {}
        self.missing: set[str] = set()
        self.reports: list[dict] = []
        self.entities: list[dict] = []
        self.manifest_hits = 0
        self.file_hits: list[str] = []
        self.force_status: int | None = None

    def _new_etag(self) -> str:
        self._etag_counter += 1
        return '"' + hashlib.sha256(str(self._etag_counter).encode()).hexdigest() + '"'

    def bump(self) -> None:
        self.etag = self._new_etag()

    def set_release(
        self, files: dict[str, bytes], media: dict[str, bytes] | None = None, **kwargs
    ) -> dict:
        release = make_release(files, media=media, **kwargs)
        for data in [*files.values(), *(media or {}).values()]:
            self.blobs[sha(data)] = data
        self.manifest["release"] = release
        self.bump()
        return release

    def set_manifest(self, **fields) -> None:
        self.manifest.update(fields)
        self.bump()

    def _auth(self, request: web.Request) -> None:
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            raise web.HTTPUnauthorized(
                text=json.dumps({"detail": "invalid token"}), content_type="application/json"
            )
        assert request.headers.get("User-Agent", "").startswith("home-labs-sync/")

    async def manifest_handler(self, request: web.Request) -> web.Response:
        self._auth(request)
        self.manifest_hits += 1
        if self.force_status:
            return web.Response(status=self.force_status, text="boom")
        if request.headers.get("If-None-Match") == self.etag:
            return web.Response(status=304)
        return web.json_response(self.manifest, headers={"ETag": self.etag})

    async def file_handler(self, request: web.Request) -> web.Response:
        self._auth(request)
        digest = request.match_info["sha"]
        self.file_hits.append(digest)
        if digest in self.missing or digest not in self.blobs:
            return web.json_response({"detail": "not found"}, status=404)
        return web.Response(body=self.blobs[digest], content_type="application/octet-stream")

    async def report_handler(self, request: web.Request) -> web.Response:
        self._auth(request)
        self.reports.append(await request.json())
        return web.Response(status=204)

    async def entities_handler(self, request: web.Request) -> web.Response:
        self._auth(request)
        body = (await request.read()).decode("utf-8")
        assert request.content_type == "text/csv"
        self.entities.append({"trigger": request.headers.get("X-HL-Trigger"), "body": body})
        if not body.startswith(HEADER + "\n"):
            return web.json_response({"detail": "bad header"}, status=422)
        return web.json_response(
            {"export_id": f"exp-{len(self.entities)}", "status": "received"}, status=202
        )

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/sync/v1/manifest", self.manifest_handler)
        app.router.add_get("/sync/v1/files/{sha}", self.file_handler)
        app.router.add_post("/sync/v1/report", self.report_handler)
        app.router.add_post("/sync/v1/entities", self.entities_handler)
        return app


class FakeSupervisor:
    def __init__(self) -> None:
        self.services: list[tuple[str, str, dict]] = []
        self.states: dict[str, dict] = {}
        self.check_ok = True
        self.check_message = ""
        self.checks = 0
        self.restarts = 0
        self.template_output = (
            HEADER + "\n"
            "light.salon;Salon;Salon;Parter;-;-;on\n"
            "sensor.temp;Temp;Salon;Parter;temperature;°C;21.5\n"
        )
        self.templates: list[str] = []
        self.version = "2026.9.1"

    def service_calls(self, domain: str, service: str) -> list[dict]:
        return [d for (dm, sv, d) in self.services if dm == domain and sv == service]

    def notifications(self, notification_id: str) -> list[dict]:
        return [
            d
            for d in self.service_calls("persistent_notification", "create")
            if d.get("notification_id") == notification_id
        ]

    async def core_info(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {"result": "ok", "data": {"version": self.version, "state": "running"}}
        )

    async def core_check(self, _request: web.Request) -> web.Response:
        self.checks += 1
        if self.check_ok:
            return web.json_response({"result": "ok", "data": {}})
        return web.json_response({"result": "error", "message": self.check_message}, status=400)

    async def core_restart(self, _request: web.Request) -> web.Response:
        self.restarts += 1
        self.states.clear()
        return web.json_response({"result": "ok", "data": {}})

    async def service(self, request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        self.services.append((request.match_info["domain"], request.match_info["service"], data))
        return web.json_response([])

    async def set_state(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.states[request.match_info["entity_id"]] = body
        return web.json_response(body)

    async def get_state(self, request: web.Request) -> web.Response:
        state = self.states.get(request.match_info["entity_id"])
        if state is None:
            return web.json_response({"message": "Entity not found."}, status=404)
        return web.json_response(state)

    async def template(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.templates.append(body["template"])
        return web.Response(text=self.template_output, content_type="text/plain")

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/core/info", self.core_info)
        app.router.add_post("/core/check", self.core_check)
        app.router.add_post("/core/restart", self.core_restart)
        app.router.add_post("/core/api/services/{domain}/{service}", self.service)
        app.router.add_post("/core/api/states/{entity_id}", self.set_state)
        app.router.add_get("/core/api/states/{entity_id}", self.get_state)
        app.router.add_post("/core/api/template", self.template)
        return app


@pytest_asyncio.fixture
async def env(tmp_path, aiohttp_server):
    admin = FakeAdmin()
    sup = FakeSupervisor()
    admin_server = await aiohttp_server(admin.app())
    sup_server = await aiohttp_server(sup.app())
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "configuration.yaml").write_text(DEFAULT_CONFIGURATION, encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Like the Supervisor's /media mount: the dir exists, ``wallpanel/`` inside it does not yet.
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    session = aiohttp.ClientSession()
    clock = SimpleNamespace(now=datetime(2026, 9, 22, 12, 0, tzinfo=UTC))

    def make_runner(
        *,
        token: str | None = None,
        supervisor: bool = True,
        media_dir: Path | None = None,
        **opts,
    ) -> SyncRunner:
        options = Options(
            api_url=str(admin_server.make_url("/")).rstrip("/"),
            api_token=admin.token if token is None else token,
            **opts,
        )
        client = AdminClient(session, options.api_url, options.api_token)
        hass = HassApi(
            session, str(sup_server.make_url("/")).rstrip("/"), "sup-token" if supervisor else ""
        )
        return SyncRunner(
            options,
            Paths(config_dir, data_dir, media_dir=media_dir or tmp_path / "media"),
            client,
            hass,
            clock=lambda: clock.now,
            restart_poll_seconds=0.01,
            restart_timeout=1.0,
        )

    yield SimpleNamespace(
        admin=admin,
        sup=sup,
        config_dir=config_dir,
        data_dir=data_dir,
        media_dir=media_dir,
        make_runner=make_runner,
        clock=clock,
        session=session,
    )
    await session.close()
