from datetime import UTC, datetime, time

from hl_sync.options import in_window

from .conftest import MANAGED, RELEASE_ID, SAMPLE_FILES


def test_in_window_handles_wrap_around_midnight():
    assert in_window("02:00-05:00", time(3, 0))
    assert not in_window("02:00-05:00", time(5, 0))
    assert not in_window("02:00-05:00", time(12, 0))
    assert in_window("22:00-05:00", time(23, 30))
    assert in_window("22:00-05:00", time(1, 0))
    assert not in_window("22:00-05:00", time(12, 0))


async def test_auto_outside_window_notifies_instead(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    env.clock.now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    runner = env.make_runner(restart_policy="auto", restart_window="02:00-05:00")

    result = await runner.run_once()

    assert result.needs_restart is True and result.restart_done is False
    assert env.sup.restarts == 0
    notes = env.sup.notifications("home_labs_sync_restart")
    assert len(notes) == 1
    assert notes[0]["title"] == "Home Labs Sync: wymagany restart Home Assistant"
    assert env.admin.reports[-1]["restart_done"] is False


async def test_auto_inside_window_restarts_once_per_release(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    env.clock.now = datetime(2026, 9, 22, 3, 0, tzinfo=UTC)
    runner = env.make_runner(restart_policy="auto", restart_window="02:00-05:00")

    result = await runner.run_once()

    assert result.restart_done is True and result.needs_restart is True
    assert env.sup.restarts == 1
    assert runner.state.restart_done_for == RELEASE_ID
    assert runner.state.pending_restart is False
    assert not env.sup.notifications("home_labs_sync_restart")
    assert env.admin.reports[-1]["restart_done"] is True
    # the sensor is re-created after the restart wiped REST states
    assert env.sup.states["sensor.home_labs_sync"]["state"] == "ok"

    # same release, lovelace part "changes" again (package removed) -> no second restart
    (env.config_dir / "home_labs/package.yaml").unlink()
    env.admin.manifest["release"]["warnings"] = ["ponownie"]
    env.admin.bump()
    result = await runner.run_once()
    assert result.needs_restart is True
    assert env.sup.restarts == 1
    assert env.sup.notifications("home_labs_sync_restart")


async def test_auto_requires_check_ok(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    env.clock.now = datetime(2026, 9, 22, 3, 0, tzinfo=UTC)
    env.sup.check_ok = False
    env.sup.check_message = "Invalid config for 'lovelace'"
    runner = env.make_runner(restart_policy="auto")

    result = await runner.run_once()

    assert result.status == "partial"
    assert result.check_result == "error"
    assert "lovelace" in result.check_output
    assert env.sup.restarts == 0
    assert env.sup.notifications("home_labs_sync_restart")
    assert env.admin.reports[-1]["check_result"] == "error"


async def test_never_policy_only_reports(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner(restart_policy="never")
    result = await runner.run_once()
    assert result.needs_restart is True
    assert env.sup.restarts == 0
    assert not env.sup.notifications("home_labs_sync_restart")
    assert env.admin.reports[-1]["needs_restart"] is True


async def test_manual_restart_clears_pending_flag(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()
    await runner.run_once()
    assert runner.state.pending_restart is True
    env.sup.states.clear()  # HA restarted: REST-set sensor is gone

    result = await runner.run_once()

    assert result.needs_restart is False
    assert runner.state.pending_restart is False
    assert env.sup.service_calls("persistent_notification", "dismiss")
    assert env.sup.states["sensor.home_labs_sync"]["attributes"]["needs_restart"] is False
