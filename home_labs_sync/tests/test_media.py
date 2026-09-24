"""Scope ``media``: WallPanel screensaver photos synced into the media dir (``/media``)."""

import json

from hl_sync.sync import MEDIA_DIR_MISSING

from .conftest import MANAGED, MEDIA_FILES, SAMPLE_FILES, sha

RELEASE_2 = "22222222-2222-2222-2222-222222222222"


def media_keys(paths=MEDIA_FILES):
    return {f"media:{p}" for p in paths}


def config_keys(paths=SAMPLE_FILES):
    return {f"config:{p}" for p in paths}


async def test_media_files_land_in_media_dir_and_are_reported(env):
    env.admin.set_release(SAMPLE_FILES, media=MEDIA_FILES, managed=MANAGED)
    runner = env.make_runner()

    result = await runner.run_once()

    assert result.status == "ok", result.errors
    assert (env.media_dir / "wallpanel").is_dir()
    for path, data in MEDIA_FILES.items():
        assert (env.media_dir / path).read_bytes() == data
        assert not (env.config_dir / path).exists()
    for path, data in SAMPLE_FILES.items():
        assert (env.config_dir / path).read_bytes() == data
        assert not (env.media_dir / path).exists()

    expected = sorted([*SAMPLE_FILES, "home_labs/package.yaml", *media_keys()])
    assert sorted(result.applied) == expected
    report = env.admin.reports[-1]
    assert report["status"] == "ok"
    assert sorted(report["applied"]) == expected
    assert report["deleted"] == [] and report["errors"] == []
    assert report["scopes"] == [
        "dashboards",
        "themes",
        "automations",
        "scenes",
        "media",
        "packages",
    ]

    state = json.loads((env.data_dir / "state.json").read_text(encoding="utf-8"))
    assert set(state["applied"]) == config_keys() | media_keys()
    assert state["applied"]["media:wallpanel/las.jpg"] == sha(MEDIA_FILES["wallpanel/las.jpg"])
    # nothing is reloaded for media: only the usual HA services were called
    assert {d for d, _, _ in env.sup.services} <= {
        "automation",
        "scene",
        "frontend",
        "persistent_notification",
    }


async def test_media_only_change_reloads_nothing(env):
    env.admin.set_release(SAMPLE_FILES, media=MEDIA_FILES, managed=MANAGED)
    runner = env.make_runner()
    await runner.run_once()
    calls_before = len(env.sup.services)
    restarts_before = env.sup.restarts

    media = dict(MEDIA_FILES)
    media["wallpanel/las.jpg"] = b"nowe zdjecie lasu"
    media["wallpanel/gory.jpg"] = b"gory"
    env.admin.set_release(SAMPLE_FILES, media=media, managed=MANAGED, release_id=RELEASE_2)
    result = await runner.run_once()

    assert result.status == "ok", result.errors
    assert sorted(result.applied) == ["media:wallpanel/gory.jpg", "media:wallpanel/las.jpg"]
    assert result.deleted == []
    assert (env.media_dir / "wallpanel/las.jpg").read_bytes() == b"nowe zdjecie lasu"
    assert (env.media_dir / "wallpanel/gory.jpg").read_bytes() == b"gory"
    reloads = [
        (d, s)
        for d, s, _ in env.sup.services[calls_before:]
        if (d, s) in {("automation", "reload"), ("scene", "reload"), ("frontend", "reload_themes")}
    ]
    assert reloads == []
    assert env.sup.restarts == restarts_before
    # the photo we overwrote was ours, so no backup copy of it was taken
    assert not (env.data_dir / "backup" / RELEASE_2 / "media").exists()


async def test_media_scope_disabled_leaves_media_alone(env):
    env.admin.set_release(SAMPLE_FILES, media=MEDIA_FILES, managed=MANAGED)
    runner = env.make_runner(scopes=["dashboards", "themes", "automations", "scenes"])

    result = await runner.run_once()

    assert result.status == "ok", result.errors
    assert not (env.media_dir / "wallpanel").exists()
    assert list(env.media_dir.iterdir()) == []
    assert not any(sha(d) in env.admin.file_hits for d in MEDIA_FILES.values())
    assert not any(p.startswith("media:") for p in result.applied)
    assert not any(k.startswith("media:") for k in runner.state.applied)
    assert env.admin.reports[-1]["scopes"] == ["dashboards", "themes", "automations", "scenes"]


async def test_disabling_media_scope_later_keeps_photos_and_state(env):
    env.admin.set_release(SAMPLE_FILES, media=MEDIA_FILES, managed=MANAGED)
    await env.make_runner().run_once()

    env.admin.set_release(SAMPLE_FILES, media={}, managed=MANAGED, release_id=RELEASE_2)
    runner = env.make_runner(scopes=["dashboards", "themes", "automations", "scenes"])
    result = await runner.run_once()

    assert result.status == "ok", result.errors
    assert result.deleted == []
    for path, data in MEDIA_FILES.items():
        assert (env.media_dir / path).read_bytes() == data
    # entries of the disabled scope are carried over untouched
    assert media_keys() <= set(runner.state.applied)


async def test_removed_media_file_deleted_only_when_written_by_us(env):
    env.admin.set_release(SAMPLE_FILES, media=MEDIA_FILES, managed=MANAGED)
    runner = env.make_runner()
    await runner.run_once()
    own_photo = env.media_dir / "wallpanel" / "wlasne.jpg"
    own_photo.write_bytes(b"zdjecie klienta")
    edited = env.media_dir / "wallpanel" / "las.jpg"
    edited.write_bytes(b"podmienione przez klienta")

    env.admin.set_release(SAMPLE_FILES, media={}, managed=MANAGED, release_id=RELEASE_2)
    result = await runner.run_once()

    assert result.status == "ok", result.errors
    assert not (env.media_dir / "wallpanel/morze.jpg").exists()
    assert edited.read_bytes() == b"podmienione przez klienta"
    assert own_photo.read_bytes() == b"zdjecie klienta"
    assert result.deleted == ["media:wallpanel/morze.jpg"]
    assert result.applied == []
    assert env.admin.reports[-1]["deleted"] == ["media:wallpanel/morze.jpg"]
    assert not any(k.startswith("media:") for k in runner.state.applied)
    assert config_keys() <= set(runner.state.applied)
    backup = env.data_dir / "backup" / RELEASE_2 / "media" / "wallpanel" / "morze.jpg"
    assert backup.read_bytes() == MEDIA_FILES["wallpanel/morze.jpg"]
    for path, data in SAMPLE_FILES.items():
        assert (env.config_dir / path).read_bytes() == data


async def test_existing_customer_photo_is_backed_up_before_overwrite(env):
    (env.media_dir / "wallpanel").mkdir()
    (env.media_dir / "wallpanel" / "las.jpg").write_bytes(b"stare zdjecie klienta")
    env.admin.set_release(SAMPLE_FILES, media=MEDIA_FILES, managed=MANAGED)

    result = await env.make_runner().run_once()

    assert result.status == "ok", result.errors
    assert (env.media_dir / "wallpanel/las.jpg").read_bytes() == MEDIA_FILES["wallpanel/las.jpg"]
    backup = env.data_dir / "backup" / result.release_id / "media" / "wallpanel" / "las.jpg"
    assert backup.read_bytes() == b"stare zdjecie klienta"


async def test_legacy_state_without_root_prefix_is_treated_as_config(env):
    """``state.json`` from add-on 0.1.x lists bare config paths: nothing is re-downloaded,
    a file that left the release is still deleted (we wrote it) and the keys get migrated."""
    for path, data in SAMPLE_FILES.items():
        target = env.config_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    stale = env.config_dir / "dashboards" / "stary.yaml"
    stale.write_text("title: Stary\n", encoding="utf-8")
    legacy = {path: sha(data) for path, data in SAMPLE_FILES.items()}
    legacy["dashboards/stary.yaml"] = sha(stale.read_bytes())
    (env.data_dir / "state.json").write_text(
        json.dumps({"applied": legacy, "release_id": "stare-wydanie", "commit": "b" * 40}),
        encoding="utf-8",
    )
    env.admin.set_release(SAMPLE_FILES, media=MEDIA_FILES, managed=MANAGED)
    runner = env.make_runner()
    assert set(runner.state.applied) == config_keys(legacy)

    result = await runner.run_once()

    assert result.status == "ok", result.errors
    assert sorted(env.admin.file_hits) == sorted(sha(d) for d in MEDIA_FILES.values())
    assert not stale.exists()
    assert result.deleted == ["dashboards/stary.yaml"]
    assert sorted(result.applied) == sorted(["home_labs/package.yaml", *media_keys()])
    for path, data in SAMPLE_FILES.items():
        assert (env.config_dir / path).read_bytes() == data
    saved = json.loads((env.data_dir / "state.json").read_text(encoding="utf-8"))
    assert set(saved["applied"]) == config_keys() | media_keys()
    assert all(k.startswith(("config:", "media:")) for k in saved["applied"])


async def test_manifest_without_root_field_is_config(env):
    """Older servers send no ``root``: every file is a config file, as before."""
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    for entry in env.admin.manifest["release"]["files"]:
        del entry["root"]
    runner = env.make_runner()

    result = await runner.run_once()

    assert result.status == "ok", result.errors
    for path, data in SAMPLE_FILES.items():
        assert (env.config_dir / path).read_bytes() == data
    assert set(runner.state.applied) == config_keys()
    assert sorted(env.admin.reports[-1]["applied"]) == sorted(
        [*SAMPLE_FILES, "home_labs/package.yaml"]
    )


async def test_missing_media_dir_is_partial_and_config_still_applied(env):
    env.admin.set_release(SAMPLE_FILES, media=MEDIA_FILES, managed=MANAGED)
    missing = env.media_dir.parent / "brak-media"
    runner = env.make_runner(media_dir=missing)

    result = await runner.run_once()

    assert result.status == "partial"
    assert result.errors == [MEDIA_DIR_MISSING.format(dir=missing)]
    assert "Katalog mediów" in result.errors[0] and "zakres media" in result.errors[0]
    for path, data in SAMPLE_FILES.items():
        assert (env.config_dir / path).read_bytes() == data
    assert (env.config_dir / "home_labs/package.yaml").exists()
    assert not missing.exists()
    assert not any(sha(d) in env.admin.file_hits for d in MEDIA_FILES.values())
    assert not any(p.startswith("media:") for p in result.applied)
    assert not any(k.startswith("media:") for k in runner.state.applied)
    assert config_keys() <= set(runner.state.applied)
    report = env.admin.reports[-1]
    assert report["status"] == "partial"
    assert report["errors"] == result.errors
    assert "media" in report["scopes"]
    assert env.sup.states["sensor.home_labs_sync"]["state"] == "error"

    # once the mount appears, the next run picks the photos up without touching config files
    missing.mkdir()
    file_hits = len(env.admin.file_hits)
    result = await runner.run_once()
    assert result.status == "ok", result.errors
    assert sorted(result.applied) == sorted(media_keys())
    assert len(env.admin.file_hits) == file_hits + len(MEDIA_FILES)
    for path, data in MEDIA_FILES.items():
        assert (missing / path).read_bytes() == data


async def test_dry_run_lists_media_without_writing(env):
    env.admin.set_release(SAMPLE_FILES, media=MEDIA_FILES, managed=MANAGED)
    runner = env.make_runner(dry_run=True)

    result = await runner.run_once()

    assert result.status == "ok" and result.dry_run
    assert list(env.media_dir.iterdir()) == []
    assert env.admin.file_hits == []
    assert media_keys() <= set(result.applied)
    assert "zapis: media:wallpanel/las.jpg" in result.plan_lines
    assert media_keys() <= set(env.admin.reports[-1]["applied"])
