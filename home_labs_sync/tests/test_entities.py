from datetime import UTC, datetime

from hl_sync import entities

from .conftest import HEADER, SAMPLE_FILES


async def test_panel_export_posts_csv_with_trigger_header(env):
    runner = env.make_runner()

    result = await runner.export_entities("panel")

    assert result.ok and result.rows == 2
    assert result.export_id == "exp-1"
    assert len(env.admin.entities) == 1
    posted = env.admin.entities[0]
    assert posted["trigger"] == "panel"
    assert posted["body"].startswith(HEADER + "\n")
    assert "light.salon;Salon;Salon;Parter;-;-;on" in posted["body"]
    assert env.sup.templates == [entities.bundled_template()]
    assert runner.state.last_entities_export_at is not None
    assert not list(env.data_dir.rglob("*.csv"))


def test_bundled_template_matches_reference():
    template = entities.bundled_template()
    assert HEADER in template
    assert "floor_name(s.entity_id)" in template
    assert "persistent_notification" in template


async def test_export_rejects_wrong_header(env):
    env.sup.template_output = "foo;bar\nx;y\n"
    runner = env.make_runner()
    result = await runner.export_entities("panel")
    assert not result.ok
    assert "nagłówek" in result.message.lower()
    assert env.admin.entities == []


async def test_export_tolerates_leading_blank_lines(env):
    env.sup.template_output = "\n\n" + HEADER + "\nlight.a;A;-;-;-;-;on\n"
    runner = env.make_runner()
    result = await runner.export_entities("schedule")
    assert result.ok and result.rows == 1
    assert env.admin.entities[0]["trigger"] == "schedule"


async def test_requested_export_runs_once(env):
    env.admin.set_manifest(requests={"entities_export": "2026-09-22T09:00:00+00:00"})
    runner = env.make_runner()

    await runner.run_once()
    assert [e["trigger"] for e in env.admin.entities] == ["requested"]
    assert runner.state.last_entities_request == "2026-09-22T09:00:00+00:00"

    await runner.run_once()  # 304
    assert len(env.admin.entities) == 1

    env.admin.set_manifest(client="testowy-2")  # new etag, same request timestamp
    await runner.run_once()
    assert len(env.admin.entities) == 1

    env.admin.set_manifest(requests={"entities_export": "2026-09-22T11:00:00+00:00"})
    await runner.run_once()
    assert len(env.admin.entities) == 2

    env.admin.set_manifest(requests={"entities_export": "2026-09-22T08:00:00+00:00"})  # older
    await runner.run_once()
    assert len(env.admin.entities) == 2


async def test_release_template_overrides_bundled(env):
    custom = HEADER + "\n{%- for s in states %}\n{{ s.entity_id }};;;;;;\n{%- endfor %}\n"
    env.admin.set_release(SAMPLE_FILES, templates={"entities_export": custom})
    runner = env.make_runner()
    await runner.run_once()
    await runner.export_entities("panel")
    assert env.sup.templates[-1] == custom
    assert runner.state.export_template == custom


def test_schedule_due_daily_and_weekly():
    tz = UTC
    now = datetime(2026, 9, 22, 4, 30, tzinfo=tz)  # Tuesday
    assert entities.schedule_due("off", 4, None, now) is False
    assert entities.schedule_due("daily", 4, None, now) is True
    assert entities.schedule_due("daily", 4, datetime(2026, 9, 21, 4, 5, tzinfo=tz), now) is True
    assert entities.schedule_due("daily", 4, datetime(2026, 9, 22, 4, 5, tzinfo=tz), now) is False
    assert entities.schedule_due("daily", 5, datetime(2026, 9, 21, 5, 5, tzinfo=tz), now) is False
    # weekly: due Monday 4:00 -> 2026-09-21 04:00
    assert entities.schedule_due("weekly", 4, datetime(2026, 9, 20, 4, 0, tzinfo=tz), now) is True
    assert entities.schedule_due("weekly", 4, datetime(2026, 9, 21, 4, 1, tzinfo=tz), now) is False
    monday_early = datetime(2026, 9, 21, 3, 0, tzinfo=tz)
    assert (
        entities.schedule_due("weekly", 4, datetime(2026, 9, 14, 4, 1, tzinfo=tz), monday_early)
        is False
    )
