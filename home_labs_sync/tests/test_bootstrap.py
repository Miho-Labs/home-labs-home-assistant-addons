from pathlib import Path

from hl_sync import bootstrap
from hl_sync.bootstrap import (
    MISSING_PACKAGES,
    MISSING_PARSE,
    MISSING_THEMES,
    Layout,
)

from .conftest import DEFAULT_CONFIGURATION, LOVELACE_FRAGMENT, MANAGED, SAMPLE_FILES

REST = """
frontend:
  themes: !include_dir_merge_named themes

automation: !include automations.yaml
scene: !include scenes.yaml
"""


def write_cfg(config_dir: Path, text: str) -> None:
    (config_dir / "configuration.yaml").write_text(text, encoding="utf-8")


async def test_layout_include_dir_named(env):
    cfg = "homeassistant:\n  packages: !include_dir_named packages\n" + REST
    write_cfg(env.config_dir, cfg)
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()

    result = await runner.run_once()

    assert result.status == "ok", result.errors
    pkg_path = env.config_dir / "packages" / "home_labs.yaml"
    assert pkg_path.exists()
    pkg = pkg_path.read_text(encoding="utf-8")
    assert "automation home_labs: !include ../home_labs/automations.yaml\n" in pkg
    assert "scene home_labs: !include ../home_labs/scenes.yaml\n" in pkg
    assert "panel-dom:" in pkg
    assert not (env.config_dir / "home_labs/package.yaml").exists()
    assert (env.config_dir / "configuration.yaml").read_text(encoding="utf-8") == cfg
    assert result.bootstrap_missing == []
    assert result.needs_restart is True
    assert "packages/home_labs.yaml" in result.applied


def test_layout_include_dir_merge_named_wraps_package(tmp_path):
    write_cfg(tmp_path, "homeassistant:\n  packages: !include_dir_merge_named packages\n" + REST)
    plan = bootstrap.inspect(tmp_path, ("dashboards", "automations"), LOVELACE_FRAGMENT)
    assert plan.layout is Layout.INCLUDE_DIR
    assert plan.package_path == "packages/home_labs.yaml"
    assert plan.include_prefix == "../home_labs/"
    assert plan.wrap_key == "home_labs"


async def test_layout_manual_leaves_configuration_untouched(env):
    cfg = "homeassistant:\n  customize: {}\n" + REST
    write_cfg(env.config_dir, cfg)
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()

    result = await runner.run_once()

    assert result.status == "ok", result.errors
    assert (env.config_dir / "configuration.yaml").read_text(encoding="utf-8") == cfg
    assert result.bootstrap_missing == [MISSING_PACKAGES]
    instruction = result.bootstrap_instructions[MISSING_PACKAGES]
    assert "packages:\n  home_labs: !include home_labs/package.yaml" in instruction
    assert (env.config_dir / "home_labs/package.yaml").exists()
    notes = env.sup.notifications("home_labs_sync_bootstrap")
    assert notes and "home_labs: !include home_labs/package.yaml" in notes[0]["message"]
    assert env.admin.reports[-1]["bootstrap_missing"] == [MISSING_PACKAGES]
    assert result.needs_restart is True
    assert not env.sup.notifications("home_labs_sync_restart")


async def test_layout_manual_packages_dict_without_our_entry(env):
    cfg = "homeassistant:\n  packages:\n    inne: !include packages/inne.yaml\n" + REST
    write_cfg(env.config_dir, cfg)
    env.admin.set_release(SAMPLE_FILES)
    runner = env.make_runner()
    result = await runner.run_once()
    assert (env.config_dir / "configuration.yaml").read_text(encoding="utf-8") == cfg
    assert result.bootstrap_missing == [MISSING_PACKAGES]
    assert (
        "  home_labs: !include home_labs/package.yaml"
        in result.bootstrap_instructions[MISSING_PACKAGES]
    )


async def test_layout_ok_existing_entry(env):
    cfg = "homeassistant:\n  packages:\n    home_labs: !include home_labs/package.yaml\n" + REST
    write_cfg(env.config_dir, cfg)
    env.admin.set_release(SAMPLE_FILES)
    runner = env.make_runner()
    result = await runner.run_once()
    assert result.status == "ok"
    assert (env.config_dir / "configuration.yaml").read_text(encoding="utf-8") == cfg
    assert result.bootstrap_missing == []
    assert (env.config_dir / "home_labs/package.yaml").exists()


async def test_bootstrap_block_is_appended_once(env):
    env.admin.set_release(SAMPLE_FILES)
    runner = env.make_runner()
    await runner.run_once()
    cfg_after_first = (env.config_dir / "configuration.yaml").read_text(encoding="utf-8")
    (env.config_dir / "home_labs/package.yaml").unlink()
    env.admin.set_manifest(warnings=["ping"])
    env.admin.manifest["release"]["warnings"] = ["ping"]
    await runner.run_once()
    assert (env.config_dir / "configuration.yaml").read_text(encoding="utf-8") == cfg_after_first
    assert cfg_after_first.count(bootstrap.MARKER_BEGIN) == 1


async def test_conflicting_lovelace_dashboard_is_dropped(env):
    cfg = DEFAULT_CONFIGURATION + (
        "\nlovelace:\n  dashboards:\n    panel-dom:\n      mode: yaml\n"
        "      filename: dashboards/dom.yaml\n      title: Dom\n"
    )
    write_cfg(env.config_dir, cfg)
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED)
    runner = env.make_runner()

    result = await runner.run_once()

    assert result.status == "ok", result.errors
    assert result.bootstrap_conflict == ["lovelace.dashboards.panel-dom"]
    pkg = (env.config_dir / "home_labs/package.yaml").read_text(encoding="utf-8")
    assert "panel-dom" not in pkg
    assert "lovelace" not in pkg
    assert "automation home_labs" in pkg
    notes = env.sup.notifications("home_labs_sync_conflict")
    assert notes and "panel-dom" in notes[0]["message"] and "lovelace" in notes[0]["message"]
    assert env.admin.reports[-1]["bootstrap_conflict"] == ["lovelace.dashboards.panel-dom"]
    assert result.needs_restart is True  # the bootstrap block was appended


def test_partial_conflict_keeps_other_dashboards(tmp_path):
    write_cfg(tmp_path, "lovelace:\n  dashboards:\n    panel-dom:\n      mode: yaml\n" + REST)
    fragment = LOVELACE_FRAGMENT + (
        "    panel-ogrod:\n      mode: yaml\n"
        "      filename: dashboards/ogrod.yaml\n      title: Ogród\n"
    )
    plan = bootstrap.inspect(tmp_path, ("dashboards",), fragment)
    assert plan.conflicts == ["lovelace.dashboards.panel-dom"]
    assert "panel-ogrod" in plan.lovelace_fragment
    assert "panel-dom" not in plan.lovelace_fragment
    assert "Ogród" in plan.lovelace_fragment


async def test_frontend_themes_missing_only_with_themes_scope(env):
    cfg = "default_config:\n\nautomation: !include automations.yaml\nscene: !include scenes.yaml\n"
    write_cfg(env.config_dir, cfg)
    env.admin.set_release(SAMPLE_FILES)
    runner = env.make_runner()
    result = await runner.run_once()
    assert MISSING_THEMES in result.bootstrap_missing
    assert (
        "themes: !include_dir_merge_named themes" in result.bootstrap_instructions[MISSING_THEMES]
    )

    plan = bootstrap.inspect(env.config_dir, ("dashboards", "automations"), "")
    assert MISSING_THEMES not in plan.missing


async def test_malformed_configuration_is_left_alone(env):
    cfg = "homeassistant:\n  packages: [\nfrontend: :\n  - broken\n"
    write_cfg(env.config_dir, cfg)
    env.admin.set_release(SAMPLE_FILES)
    runner = env.make_runner()
    result = await runner.run_once()
    assert (env.config_dir / "configuration.yaml").read_text(encoding="utf-8") == cfg
    assert result.bootstrap_missing == [MISSING_PARSE]
    assert (env.config_dir / "dashboards/dom.yaml").exists()


def test_marker_present_counts_as_ok_even_when_parse_fails(tmp_path):
    write_cfg(tmp_path, "broken: [\n" + bootstrap.BOOTSTRAP_BLOCK)
    plan = bootstrap.inspect(tmp_path, ("dashboards",), LOVELACE_FRAGMENT)
    assert plan.layout is Layout.OK
    assert plan.append_block is False
    assert plan.missing == []


def test_packages_include_single_file_is_manual(tmp_path):
    write_cfg(tmp_path, "homeassistant:\n  packages: !include packages.yaml\n" + REST)
    plan = bootstrap.inspect(tmp_path, ("dashboards",), "")
    assert plan.layout is Layout.MANUAL
    assert plan.missing == [MISSING_PACKAGES]


def test_include_dir_escaping_config_dir_is_manual(tmp_path):
    write_cfg(tmp_path, "homeassistant:\n  packages: !include_dir_named ../outside\n" + REST)
    plan = bootstrap.inspect(tmp_path, ("dashboards",), "")
    assert plan.layout is Layout.MANUAL
