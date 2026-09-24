"""Scope ``packages``: HA package YAML (clients/<slug>/packages/) inside the home_labs package."""

import pytest
import yaml

from hl_sync.applier import compose_package, extract_packages
from hl_sync.hlyaml import Tagged, load_tolerant
from hl_sync.manifest import ManifestError, validate_manifest

from .conftest import MANAGED, SAMPLE_FILES, base_manifest, make_release

PACKAGES = (
    "# --- packages/klimatyzacja_poddasze.yaml\n"
    "climate:\n"
    "  - platform: climate_template\n"
    "    name: Klimatyzacja poddasze\n"
    "    unique_id: klimatyzacja_poddasze\n"
    "script:\n"
    "  klimatyzacja_poddasze_wyslij:\n"
    "    sequence:\n"
    "      - action: remote.send_command\n"
    "        data:\n"
    "          command: cool_24\n"
    "notify:\n"
    "  - platform: group\n"
    "    name: rodzina\n"
    "    services:\n"
    "      - service: mobile_app_telefon\n"
    "rest_command:\n"
    "  ping:\n"
    "    url: !secret ping_url\n"
)


def release_with(packages, **kwargs):
    return validate_manifest(base_manifest(make_release(SAMPLE_FILES, packages=packages, **kwargs)))


# ------------------------------------------------------------------ manifest


def test_manifest_accepts_packages_fragment_with_secret():
    rel = release_with(PACKAGES).release
    assert rel.packages_fragment == PACKAGES


def test_manifest_without_packages_is_empty_fragment():
    rel = release_with(None).release
    assert rel.packages_fragment == ""
    assert release_with("  \n").release.packages_fragment == ""


def test_manifest_packages_fragment_gets_trailing_newline():
    assert release_with("input_boolean:\n  goscie: {}").release.packages_fragment.endswith("}\n")


@pytest.mark.parametrize(
    "text",
    [
        "homeassistant:\n  customize: {}\n",
        "lovelace:\n  mode: yaml\n",
        "frontend:\n  extra_module_url: []\n",
        "http:\n  server_port: 8124\n",
        "shell_command:\n  x: rm -rf /\n",
        "command_line:\n  - sensor:\n      command: id\n",
        "command_line nasz:\n  - sensor:\n      command: id\n",
        "python_script:\n",
        "automation home_labs:\n  - id: x\n",
        "scene home_labs:\n  - id: x\n",
    ],
)
def test_manifest_rejects_forbidden_or_reserved_keys(text):
    with pytest.raises(ManifestError):
        release_with(text)


@pytest.mark.parametrize(
    "text",
    [
        "script: !include scripts.yaml\n",
        "sensor: !include_dir_merge_list sensors\n",
        "rest_command:\n  x:\n    url: !env_var URL\n",
        "rest_command:\n  x:\n    url: !secret [a, b]\n",
    ],
)
def test_manifest_rejects_tags_other_than_secret(text):
    with pytest.raises(ManifestError):
        release_with(text)


@pytest.mark.parametrize("value", ["- climate: []\n", "tylko tekst\n", "a: [\n", 42])
def test_manifest_rejects_non_mapping_or_broken_packages(value):
    with pytest.raises(ManifestError):
        release_with(value)


def test_manifest_rejects_unknown_package_key():
    raw = make_release(SAMPLE_FILES)
    raw["package"]["cokolwiek"] = "x: 1\n"
    with pytest.raises(ManifestError):
        validate_manifest(base_manifest(raw))


# ------------------------------------------------------------------ compose


class Layout:
    include_prefix = ""
    wrap_key = None


class WrapLayout:
    include_prefix = "../home_labs/"
    wrap_key = "home_labs"


def test_compose_appends_packages_after_generated_lines():
    rel = release_with(PACKAGES, managed=MANAGED).release
    text = compose_package(rel, ("dashboards", "automations", "scenes", "packages"), Layout())
    data = load_tolerant(text)
    assert set(data) == {
        "lovelace",
        "automation home_labs",
        "scene home_labs",
        "climate",
        "script",
        "notify",
        "rest_command",
    }
    assert data["rest_command"]["ping"]["url"] == Tagged("!secret", "ping_url")
    assert extract_packages(text, None) == load_tolerant(PACKAGES)


def test_compose_skips_packages_when_scope_disabled():
    rel = release_with(PACKAGES, managed=MANAGED).release
    text = compose_package(rel, ("dashboards", "automations", "scenes"), Layout())
    assert "climate" not in load_tolerant(text)
    assert extract_packages(text, None) == {}


def test_compose_indents_packages_under_wrap_key():
    rel = release_with(PACKAGES, managed=MANAGED).release
    text = compose_package(rel, ("dashboards", "automations", "scenes", "packages"), WrapLayout())
    data = load_tolerant(text)
    assert set(data) == {"home_labs"}
    assert data["home_labs"]["script"]["klimatyzacja_poddasze_wyslij"]
    assert data["home_labs"]["automation home_labs"] == Tagged(
        "!include", "../home_labs/automations.yaml"
    )
    assert extract_packages(text, "home_labs") == load_tolerant(PACKAGES)


# ------------------------------------------------------------------ sync flow


async def test_first_sync_writes_packages_and_asks_for_restart(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED, packages=PACKAGES)
    runner = env.make_runner(restart_policy="never")

    result = await runner.run_once("startup")

    assert result.status == "ok", result.errors
    pkg = (env.config_dir / "home_labs/package.yaml").read_text(encoding="utf-8")
    assert "unique_id: klimatyzacja_poddasze" in pkg
    assert "url: !secret ping_url" in pkg
    assert yaml.safe_load(pkg.replace("!include ", "").replace("!secret ", ""))["climate"]
    assert result.needs_restart is True
    assert env.admin.reports[-1]["scopes"][-1] == "packages"


async def test_changed_packages_need_restart_unchanged_do_not(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED, packages=PACKAGES)
    runner = env.make_runner(restart_policy="never")
    await runner.run_once()
    runner.state.pending_restart = False  # as if HA was restarted after the first sync

    # Only a dashboard changes: package.yaml stays the same, no restart.
    files = {**SAMPLE_FILES, "dashboards/dom.yaml": b"title: Dom 2\nviews: []\n"}
    env.admin.set_release(files, managed=MANAGED, packages=PACKAGES, release_id="r-2")
    result = await runner.run_once()
    assert result.status == "ok", result.errors
    assert "home_labs/package.yaml" not in result.applied
    assert result.needs_restart is False

    # The packages change: package.yaml is rewritten and HA must restart.
    changed = PACKAGES.replace("cool_24", "cool_22")
    env.admin.set_release(files, managed=MANAGED, packages=changed, release_id="r-3")
    result = await runner.run_once()
    assert result.status == "ok", result.errors
    assert "home_labs/package.yaml" in result.applied
    assert result.needs_restart is True
    pkg = (env.config_dir / "home_labs/package.yaml").read_text(encoding="utf-8")
    assert "cool_22" in pkg and "cool_24" not in pkg


async def test_packages_removed_from_release_are_dropped_from_package(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED, packages=PACKAGES)
    runner = env.make_runner(restart_policy="never")
    await runner.run_once()
    runner.state.pending_restart = False

    env.admin.set_release(SAMPLE_FILES, managed=MANAGED, release_id="r-2")
    result = await runner.run_once()

    assert result.status == "ok", result.errors
    pkg = (env.config_dir / "home_labs/package.yaml").read_text(encoding="utf-8")
    assert "climate" not in pkg and "lovelace:" in pkg
    assert result.needs_restart is True


async def test_disabled_packages_scope_warns_and_skips(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED, packages=PACKAGES)
    runner = env.make_runner(scopes=["dashboards", "themes", "automations", "scenes"])

    result = await runner.run_once()

    assert result.status == "ok", result.errors
    pkg = (env.config_dir / "home_labs/package.yaml").read_text(encoding="utf-8")
    assert "climate" not in pkg
    assert any("zakres 'packages'" in w for w in result.warnings)


async def test_dry_run_with_packages_writes_nothing_but_flags_restart(env):
    env.admin.set_release(SAMPLE_FILES, managed=MANAGED, packages=PACKAGES)
    runner = env.make_runner(dry_run=True)

    result = await runner.run_once()

    assert not (env.config_dir / "home_labs/package.yaml").exists()
    assert result.needs_restart is True
    assert "zapis pakietu: home_labs/package.yaml" in result.plan_lines
