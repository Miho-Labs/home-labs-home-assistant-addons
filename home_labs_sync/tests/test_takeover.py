import os
from pathlib import Path

import yaml

from hl_sync import takeover
from hl_sync.options import SCOPES

AUTOMATIONS = """- id: '1789848132433'
  alias: Home Labs alarm
  triggers: []
  actions: []
- id: '1700000000000'
  alias: Moja własna
  description: Zażółć gęślą jaźń
  triggers:
    - trigger: state
      entity_id: binary_sensor.drzwi
  actions: []
- id: 1790001000001
  alias: HL druga
  triggers: []
  actions: []
"""
SCENES = """- id: homelabs_kule_baltyk
  name: Kule Bałtyk
  entities: {}
- id: moja_scena
  name: Moja scena
  entities:
    light.salon: 'on'
"""
MANAGED = {"automations": ["1789848132433", "1790001000001"], "scenes": ["homelabs_kule_baltyk"]}


def setup(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "config"
    config.mkdir()
    (config / "automations.yaml").write_text(AUTOMATIONS, encoding="utf-8")
    (config / "scenes.yaml").write_text(SCENES, encoding="utf-8")
    return config, tmp_path / "backup"


def test_takeover_removes_managed_entries_and_backs_up(tmp_path):
    config, backup = setup(tmp_path)

    result = takeover.run(config, backup, MANAGED, SCOPES)

    autos = yaml.safe_load((config / "automations.yaml").read_text(encoding="utf-8"))
    assert [a["id"] for a in autos] == ["1700000000000"]
    assert autos[0]["description"] == "Zażółć gęślą jaźń"
    scenes = yaml.safe_load((config / "scenes.yaml").read_text(encoding="utf-8"))
    assert [s["id"] for s in scenes] == ["moja_scena"]
    assert (backup / "automations.yaml").read_text(encoding="utf-8") == AUTOMATIONS
    assert (backup / "scenes.yaml").read_text(encoding="utf-8") == SCENES
    assert result.changed_scopes == {"automations", "scenes"}
    assert result.aborted == []
    ids = {d.domain: d.removed_ids for d in result.domains}
    assert ids["automations"] == ["1789848132433", "1790001000001"]
    assert ids["scenes"] == ["homelabs_kule_baltyk"]


def test_takeover_is_noop_when_nothing_managed_is_present(tmp_path):
    config, backup = setup(tmp_path)
    before = (config / "automations.yaml").stat().st_mtime_ns
    result = takeover.run(config, backup, {"automations": ["999"], "scenes": []}, SCOPES)
    assert result.changed_scopes == set()
    assert (config / "automations.yaml").stat().st_mtime_ns == before
    assert not backup.exists()


def test_takeover_aborts_when_file_changes_between_read_and_write(tmp_path):
    config, backup = setup(tmp_path)
    edited = (
        AUTOMATIONS + "- id: '1799999999999'\n  alias: Dodana w UI\n  triggers: []\n  actions: []\n"
    )

    def edit(path: Path) -> None:
        if path.name != "automations.yaml":
            return
        path.write_text(edited, encoding="utf-8")
        os.utime(path, ns=(1, 1))

    result = takeover.run(config, backup, MANAGED, SCOPES, on_before_write=edit)

    assert result.aborted == ["automations"]
    assert (config / "automations.yaml").read_text(encoding="utf-8") == edited
    assert "automations" not in result.changed_scopes
    assert "scenes" in result.changed_scopes


def test_takeover_respects_scopes(tmp_path):
    config, backup = setup(tmp_path)
    result = takeover.run(config, backup, MANAGED, ("dashboards", "scenes"))
    assert (config / "automations.yaml").read_text(encoding="utf-8") == AUTOMATIONS
    assert result.changed_scopes == {"scenes"}


def test_takeover_skips_non_list_and_tagged_files(tmp_path):
    config, backup = setup(tmp_path)
    (config / "automations.yaml").write_text("foo: bar\n", encoding="utf-8")
    (config / "scenes.yaml").write_text(
        "- id: homelabs_kule_baltyk\n  name: x\n  entities: !secret scena\n", encoding="utf-8"
    )
    result = takeover.run(config, backup, MANAGED, SCOPES)
    assert result.changed_scopes == set()
    assert len(result.warnings) == 2
    assert (config / "automations.yaml").read_text(encoding="utf-8") == "foo: bar\n"


def test_takeover_handles_missing_and_empty_files(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "scenes.yaml").write_text("", encoding="utf-8")
    result = takeover.run(config, tmp_path / "b", MANAGED, SCOPES)
    assert result.changed_scopes == set()
    assert result.warnings == []
