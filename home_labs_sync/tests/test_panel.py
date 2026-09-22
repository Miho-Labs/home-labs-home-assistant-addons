from hl_sync import panel

from .conftest import MANAGED, MEDIA_FILES, RELEASE_ID, SAMPLE_FILES


class Ctx:
    def __init__(self, runner):
        self.runner = runner
        self.synced = 0
        self.exported = 0

    def request_sync(self):
        self.synced += 1

    def request_export(self):
        self.exported += 1

    def log_lines(self, count):
        return ["linia 1 <b>", "linia 2"][-count:]

    def next_sync_in(self):
        return 600


async def test_panel_renders_status_and_uses_ingress_prefix(env, aiohttp_client):
    env.admin.set_release(SAMPLE_FILES, media=MEDIA_FILES, managed=MANAGED)
    runner = env.make_runner()
    await runner.run_once()
    await runner.export_entities("panel")
    ctx = Ctx(runner)
    client = await aiohttp_client(panel.create_app(ctx))
    prefix = "/api/hassio_ingress/abc123"

    resp = await client.get("/", headers={"X-Ingress-Path": prefix})

    assert resp.status == 200
    text = await resp.text()
    assert "Home Labs Sync" in text
    assert RELEASE_ID in text
    assert f'action="{prefix}/action/sync"' in text
    assert f'action="{prefix}/action/export-entities"' in text
    assert "Synchronizuj teraz" in text and "Eksportuj encje teraz" in text
    assert "wymagany restart Home Assistant" in text
    assert "linia 1 &lt;b&gt;" in text
    assert "Wysłano 2 encji" in text
    assert "dashboards, themes, automations, scenes, media" in text
    assert "<code>media:wallpanel/las.jpg</code>" in text
    assert "<code>dashboards/dom.yaml</code>" in text
    assert env.admin.token not in text


async def test_panel_buttons_redirect_and_signal(env, aiohttp_client):
    runner = env.make_runner()
    ctx = Ctx(runner)
    client = await aiohttp_client(panel.create_app(ctx))
    prefix = "/api/hassio_ingress/xyz"

    resp = await client.post(
        "/action/sync", headers={"X-Ingress-Path": prefix}, allow_redirects=False
    )
    assert resp.status == 303
    assert resp.headers["Location"].startswith(prefix + "/")
    resp = await client.post("/action/export-entities", allow_redirects=False)
    assert resp.status == 303
    assert ctx.synced == 1 and ctx.exported == 1

    resp = await client.get("/")
    assert resp.status == 200
    assert "jeszcze nie synchronizowano" in await resp.text()


async def test_panel_shows_bootstrap_instructions(env, aiohttp_client):
    (env.config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  customize: {}\n", encoding="utf-8"
    )
    env.admin.set_release(SAMPLE_FILES)
    runner = env.make_runner()
    await runner.run_once()
    client = await aiohttp_client(panel.create_app(Ctx(runner)))
    text = await (await client.get("/")).text()
    assert "do uzupełnienia ręcznie" in text
    assert "home_labs: !include home_labs/package.yaml" in text
