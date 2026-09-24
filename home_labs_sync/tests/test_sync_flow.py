import json
from datetime import timedelta

from hl_sync import VERSION
from hl_sync.bootstrap import MARKER_BEGIN, MARKER_END

from .conftest import DEFAULT_CONFIGURATION, MANAGED, RELEASE_ID, SAMPLE_FILES, sha


def write_cfg(config_dir, text: str) -> None:
    (config_dir / "configuration.yaml").write_text(text, encoding="utf-8")


REPORT_KEYS = {
    "addon_version",
    "ha_version",
    "release_id",
    "status",
    "applied",
    "deleted",
    "errors",
    "needs_restart",
    "restart_done",
    "check_result",
    "check_output",
    "bootstrap_missing",
    "bootstrap_conflict",
    "scopes",
    "dry_run",
    "duration_ms",
}


def mtimes(config_dir):
    return {p: p.stat().st_mtime_ns for p in config_dir.rglob("*") if p.is_file()}


async def test_first_sync_writes_everything(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()

    result = await runner.run_once("startup")

    assert result.status == "ok", result.errors
    for path, data in SAMPLE_FILES.items():
        assert (env.config_dir / path).read_bytes() == data
    pkg = (env.config_dir / "home_labs/package.yaml").read_text(encoding="utf-8")
    assert pkg.startswith("# home_labs package — zarządzane przez dodatek Home Labs Sync")
    assert "lovelace:" in pkg and "panel-dom:" in pkg
    assert "automation home_labs: !include automations.yaml\n" in pkg
    assert "scene home_labs: !include scenes.yaml\n" in pkg

    cfg = (env.config_dir / "configuration.yaml").read_text(encoding="utf-8")
    assert cfg.startswith(DEFAULT_CONFIGURATION)
    assert MARKER_BEGIN in cfg and cfg.rstrip().endswith(MARKER_END)
    assert "homeassistant:\n  packages:\n    home_labs: !include home_labs/package.yaml\n" in cfg
    assert result.needs_restart is True
    assert result.bootstrap_missing == [] and result.bootstrap_conflict == []

    assert len(env.admin.reports) == 1
    report = env.admin.reports[0]
    assert set(report) == REPORT_KEYS
    assert report["status"] == "ok"
    assert report["release_id"] == RELEASE_ID
    assert report["needs_restart"] is True and report["restart_done"] is False
    assert sorted(report["applied"]) == sorted([*SAMPLE_FILES, "home_labs/package.yaml"])
    assert report["deleted"] == [] and report["errors"] == []
    assert report["scopes"] == [
        "dashboards",
        "themes",
        "automations",
        "scenes",
        "media",
        "packages",
    ]
    assert report["check_result"] == "ok" and report["dry_run"] is False
    assert report["ha_version"] == "2026.9.1" and report["addon_version"] == VERSION

    assert env.sup.service_calls("automation", "reload")
    assert env.sup.service_calls("scene", "reload")
    assert env.sup.service_calls("frontend", "reload_themes")
    assert env.sup.checks == 1
    assert env.sup.restarts == 0
    assert env.sup.notifications("home_labs_sync_restart")
    assert not env.sup.notifications("home_labs_sync_bootstrap")
    assert not env.sup.notifications("home_labs_sync_conflict")
    sensor = env.sup.states["sensor.home_labs_sync"]
    assert sensor["state"] == "ok"
    assert sensor["attributes"]["release_id"] == RELEASE_ID
    assert sensor["attributes"]["needs_restart"] is True

    state = json.loads((env.data_dir / "state.json").read_text(encoding="utf-8"))
    assert state["etag"] == env.admin.etag
    assert set(state["applied"]) == {f"config:{p}" for p in SAMPLE_FILES}
    assert state["pending_restart"] is True
    assert not (env.data_dir / "stage" / RELEASE_ID).exists()
    backup = env.data_dir / "backup" / RELEASE_ID / "configuration.yaml"
    assert backup.read_text(encoding="utf-8") == DEFAULT_CONFIGURATION


async def test_second_sync_304_writes_nothing_and_reports_only_when_stale(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()
    await runner.run_once()
    before = mtimes(env.config_dir)
    hits = env.admin.manifest_hits

    result = await runner.run_once()

    assert result.status == "noop" and result.not_modified
    assert env.admin.manifest_hits == hits + 1
    assert mtimes(env.config_dir) == before
    assert len(env.admin.reports) == 1
    assert len(env.admin.file_hits) == len(SAMPLE_FILES)
    assert env.sup.states["sensor.home_labs_sync"]["state"] == "noop"

    env.clock.now += timedelta(hours=25)
    result = await runner.run_once()
    assert result.status == "noop"
    assert len(env.admin.reports) == 2
    assert env.admin.reports[-1]["status"] == "noop"
    assert env.admin.reports[-1]["release_id"] == RELEASE_ID


async def test_removed_file_deleted_only_when_written_by_us(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()
    await runner.run_once()
    customer_file = env.config_dir / "dashboards" / "wlasny.yaml"
    customer_file.write_text("title: Mój\n", encoding="utf-8")

    files = dict(SAMPLE_FILES)
    del files["dashboards/common/badges.yaml"]
    env.admin.set_release(files, managed=MANAGED, release_id="22222222-2222-2222-2222-222222222222")
    result = await runner.run_once()

    assert result.status == "ok", result.errors
    assert not (env.config_dir / "dashboards/common/badges.yaml").exists()
    assert customer_file.exists()
    assert result.deleted == ["dashboards/common/badges.yaml"]
    assert env.admin.reports[-1]["deleted"] == ["dashboards/common/badges.yaml"]
    assert result.applied == []
    assert "config:dashboards/common/badges.yaml" not in runner.state.applied
    backup = env.data_dir / "backup" / "22222222-2222-2222-2222-222222222222"
    assert (backup / "dashboards/common/badges.yaml").exists()


async def test_locally_modified_file_is_not_deleted(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()
    await runner.run_once()
    target = env.config_dir / "dashboards/common/badges.yaml"
    target.write_text("# zmienione przez klienta\n", encoding="utf-8")

    files = dict(SAMPLE_FILES)
    del files["dashboards/common/badges.yaml"]
    env.admin.set_release(files, managed=MANAGED, release_id="2" * 36)
    result = await runner.run_once()

    assert result.status == "ok"
    assert target.exists()
    assert result.deleted == []
    assert "config:dashboards/common/badges.yaml" not in runner.state.applied


async def test_disabled_scope_is_left_alone(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    (env.config_dir / "automations.yaml").write_text(
        "- id: '1789848132433'\n  alias: HL\n  triggers: []\n  actions: []\n", encoding="utf-8"
    )
    runner = env.make_runner(scopes=["dashboards", "themes", "scenes"])

    result = await runner.run_once()

    assert result.status == "ok", result.errors
    assert not (env.config_dir / "home_labs/automations.yaml").exists()
    pkg = (env.config_dir / "home_labs/package.yaml").read_text(encoding="utf-8")
    assert "automation home_labs" not in pkg
    assert "scene home_labs: !include scenes.yaml" in pkg
    assert "1789848132433" in (env.config_dir / "automations.yaml").read_text(encoding="utf-8")
    assert not env.sup.service_calls("automation", "reload")
    assert env.sup.service_calls("scene", "reload")
    assert env.admin.reports[-1]["scopes"] == ["dashboards", "themes", "scenes"]


async def test_dry_run_writes_nothing_but_reports_plan(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner(dry_run=True)

    result = await runner.run_once()

    assert result.status == "ok" and result.dry_run
    assert sorted(p.relative_to(env.config_dir).as_posix() for p in env.config_dir.rglob("*")) == [
        "configuration.yaml"
    ]
    assert (env.config_dir / "configuration.yaml").read_text(
        encoding="utf-8"
    ) == DEFAULT_CONFIGURATION
    assert env.admin.file_hits == []
    report = env.admin.reports[-1]
    assert report["dry_run"] is True
    assert sorted(report["applied"]) == sorted([*SAMPLE_FILES, "home_labs/package.yaml"])
    assert report["needs_restart"] is True
    assert runner.state.etag is None
    assert runner.state.applied == {}
    assert env.sup.states["sensor.home_labs_sync"]["state"] == "dry_run"
    assert not env.sup.service_calls("persistent_notification", "create")


async def test_failed_blob_download_writes_nothing(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    env.admin.missing.add(sha(SAMPLE_FILES["themes/homelabs.yaml"]))
    runner = env.make_runner()

    result = await runner.run_once()

    assert result.status == "error"
    assert any("themes/homelabs.yaml" in e for e in result.errors)
    for path in SAMPLE_FILES:
        assert not (env.config_dir / path).exists()
    assert not (env.config_dir / "home_labs/package.yaml").exists()
    assert (env.config_dir / "configuration.yaml").read_text(
        encoding="utf-8"
    ) == DEFAULT_CONFIGURATION
    assert not (env.data_dir / "stage" / RELEASE_ID).exists()
    report = env.admin.reports[-1]
    assert report["status"] == "error" and report["applied"] == [] and report["errors"]
    assert runner.state.etag is None and runner.state.applied == {}
    assert env.sup.states["sensor.home_labs_sync"]["state"] == "error"


async def test_sha_mismatch_is_rejected(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    digest = sha(SAMPLE_FILES["dashboards/dom.yaml"])
    env.admin.blobs[digest] = b"tampered"
    runner = env.make_runner()

    result = await runner.run_once()

    assert result.status == "error"
    assert not (env.config_dir / "dashboards/dom.yaml").exists()


async def test_null_release_reports_noop(env):
    runner = env.make_runner()
    result = await runner.run_once()
    assert result.status == "noop"
    assert env.admin.reports[-1]["status"] == "noop"
    assert env.admin.reports[-1]["release_id"] is None
    assert runner.state.etag == env.admin.etag


async def test_paused_manifest_does_nothing(env):
    env.admin.set_release(SAMPLE_FILES)
    env.admin.set_manifest(paused=True)
    runner = env.make_runner()
    result = await runner.run_once()
    assert result.status == "paused"
    assert env.admin.reports == []
    assert not (env.config_dir / "dashboards/dom.yaml").exists()
    assert env.sup.states["sensor.home_labs_sync"]["state"] == "paused"


async def test_unreachable_server_counts_failures_without_reports(env):
    env.admin.force_status = 500
    runner = env.make_runner()
    for expected in (1, 2):
        result = await runner.run_once()
        assert result.status == "unreachable"
        assert runner.state.consecutive_failures == expected
        assert env.sup.states["sensor.home_labs_sync"]["state"] == "noop"
    result = await runner.run_once()
    assert runner.state.consecutive_failures == 3
    assert env.sup.states["sensor.home_labs_sync"]["state"] == "error"
    assert env.admin.reports == []
    env.admin.force_status = None
    result = await runner.run_once()
    assert result.status == "noop" and runner.state.consecutive_failures == 0


async def test_bad_token_is_an_error_without_report(env):
    runner = env.make_runner(token="hls_wrong")
    result = await runner.run_once()
    assert result.status == "error"
    assert "token" in result.errors[0].lower()
    assert env.admin.reports == []


async def test_missing_token_short_circuits(env):
    runner = env.make_runner(token="")
    result = await runner.run_once()
    assert result.status == "error"
    assert env.admin.manifest_hits == 0


async def test_invalid_manifest_reports_error_and_writes_nothing(env):
    env.admin.set_release(SAMPLE_FILES)
    env.admin.manifest["release"]["files"].append(
        {"path": "packages/evil.yaml", "scope": "dashboards", "sha256": "0" * 64, "size": 1}
    )
    runner = env.make_runner()
    result = await runner.run_once()
    assert result.status == "error"
    assert env.admin.reports[-1]["status"] == "error"
    assert env.admin.reports[-1]["release_id"] == RELEASE_ID
    assert not (env.config_dir / "dashboards/dom.yaml").exists()


async def test_standalone_mode_without_supervisor_still_writes(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner(supervisor=False)
    result = await runner.run_once()
    assert result.status == "ok", result.errors
    assert (env.config_dir / "dashboards/dom.yaml").exists()
    assert result.check_result == "skipped"
    assert env.sup.services == []
    assert env.admin.reports[-1]["ha_version"] == "unknown"


async def test_304_recomposes_package_after_customer_removes_old_lovelace_block(env):
    """Migration of an existing client: the pasted lovelace: block wins at first, the
    customer deletes it and restarts — the next cycle (304) must put the dashboards
    into the package without waiting for a new release."""
    cfg = DEFAULT_CONFIGURATION + (
        "\nlovelace:\n  dashboards:\n    panel-dom:\n      mode: yaml\n"
        "      filename: dashboards/dom.yaml\n      title: Dom\n"
    )
    write_cfg(env.config_dir, cfg)
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()
    first = await runner.run_once()
    assert first.bootstrap_conflict == ["lovelace.dashboards.panel-dom"]
    pkg_path = env.config_dir / "home_labs/package.yaml"
    assert "panel-dom" not in pkg_path.read_text(encoding="utf-8")
    reports = len(env.admin.reports)

    # The owner follows the notification: drops the old block, keeps our marker block.
    text = (env.config_dir / "configuration.yaml").read_text(encoding="utf-8")
    head, marker, tail = text.partition("# >>> home-labs-sync")
    head = head.split("\nlovelace:")[0] + "\n"
    write_cfg(env.config_dir, head + marker + tail)

    second = await runner.run_once()

    assert second.not_modified and env.admin.manifest_hits == 2
    assert second.status == "ok", second.errors
    assert second.bootstrap_conflict == []
    pkg = pkg_path.read_text(encoding="utf-8")
    assert "panel-dom" in pkg and "lovelace:" in pkg
    assert second.needs_restart is True
    assert "home_labs/package.yaml" in second.applied
    assert len(env.admin.reports) == reports + 1
    assert env.admin.reports[-1]["needs_restart"] is True

    third = await runner.run_once()
    assert third.status == "noop" and third.not_modified
    assert len(env.admin.reports) == reports + 1


async def test_304_restores_a_synced_file_the_customer_deleted(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()
    await runner.run_once()
    victim = env.config_dir / "dashboards" / "dom.yaml"
    victim.unlink()
    file_hits = len(env.admin.file_hits)

    result = await runner.run_once()

    assert result.not_modified and result.status == "ok"
    assert victim.exists()
    assert "dashboards/dom.yaml" in result.applied
    assert len(env.admin.file_hits) == file_hits + 1


async def test_panel_button_forces_a_full_manifest_fetch(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()
    await runner.run_once()

    scheduled = await runner.run_once("schedule")
    assert scheduled.not_modified

    manual = await runner.run_once("panel")
    assert manual.not_modified is False
    assert manual.status in ("ok", "noop")


async def test_cached_release_survives_a_restart_of_the_addon(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    await env.make_runner().run_once()
    (env.config_dir / "dashboards" / "dom.yaml").unlink()

    result = await env.make_runner().run_once()  # fresh runner, state loaded from disk

    assert result.not_modified and (env.config_dir / "dashboards" / "dom.yaml").exists()
