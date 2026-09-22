"""Inspect ``configuration.yaml`` and decide how the ``home_labs`` package gets included."""

from __future__ import annotations

import logging
import os
import posixpath
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml

from .fsutil import UnsafePathError, backup_file, is_forbidden, normalize_rel
from .hlyaml import Tagged, load_tolerant
from .manifest import PACKAGE_PATH

log = logging.getLogger(__name__)

MARKER_BEGIN = "# >>> home-labs-sync"
MARKER_END = "# <<< home-labs-sync"
BOOTSTRAP_BLOCK = (
    "\n"
    "# >>> home-labs-sync (zarządzane przez dodatek Home Labs Sync — nie edytuj tego bloku)\n"
    "homeassistant:\n"
    "  packages:\n"
    "    home_labs: !include home_labs/package.yaml\n"
    "# <<< home-labs-sync\n"
)

MISSING_PACKAGES = "homeassistant.packages.home_labs"
MISSING_THEMES = "frontend.themes"
MISSING_PARSE = "configuration.yaml (nie udało się sparsować)"

INSTRUCTION_PACKAGES_UNDER_HOMEASSISTANT = (
    "W configuration.yaml, pod istniejącym kluczem `homeassistant:`, dodaj:\n"
    "packages:\n"
    "  home_labs: !include home_labs/package.yaml"
)
INSTRUCTION_PACKAGES_ENTRY = (
    "W configuration.yaml, pod `homeassistant:` → `packages:`, dodaj wiersz:\n"
    "  home_labs: !include home_labs/package.yaml"
)
INSTRUCTION_PACKAGES_MANUAL = (
    "Sekcja `homeassistant: packages:` w configuration.yaml ma postać, której dodatek nie "
    "potrafi bezpiecznie rozszerzyć. Dołącz pakiet ręcznie, np.:\n"
    "homeassistant:\n"
    "  packages:\n"
    "    home_labs: !include home_labs/package.yaml"
)
INSTRUCTION_THEMES = (
    "W configuration.yaml dodaj (albo uzupełnij sekcję `frontend:`):\n"
    "frontend:\n"
    "  themes: !include_dir_merge_named themes"
)
INSTRUCTION_PARSE = (
    "Nie udało się sparsować configuration.yaml — popraw błędy składni (Ustawienia → System → "
    "Naprawy albo Narzędzia deweloperskie → YAML → Sprawdź konfigurację). Upewnij się, że plik "
    "zawiera:\n"
    "homeassistant:\n"
    "  packages:\n"
    "    home_labs: !include home_labs/package.yaml"
)


class Layout(StrEnum):
    APPEND = "append"  # no ``homeassistant:`` key: we append the marker block
    INCLUDE_DIR = "include_dir"  # ``packages: !include_dir_named <dir>`` -> <dir>/home_labs.yaml
    OK = "ok"  # ``packages.home_labs`` already points at our file
    MANUAL = "manual"  # owner has to paste lines by hand
    PARSE_ERROR = "parse_error"


@dataclass
class BootstrapPlan:
    layout: Layout
    package_path: str = PACKAGE_PATH
    include_prefix: str = ""
    wrap_key: str | None = None
    append_block: bool = False
    missing: list[str] = field(default_factory=list)
    instructions: dict[str, str] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    lovelace_fragment: str = ""
    warnings: list[str] = field(default_factory=list)

    def add_missing(self, key: str, instruction: str) -> None:
        if key not in self.missing:
            self.missing.append(key)
        self.instructions[key] = instruction


def _include_prefix(package_rel: str) -> str:
    """Prefix for ``!include`` of files in ``home_labs/`` from the package's directory."""
    package_dir = posixpath.dirname(package_rel) or "."
    rel = os.path.relpath("home_labs", start=package_dir).replace(os.sep, "/")
    return "" if rel == "." else rel.rstrip("/") + "/"


def _safe_rel(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        norm = normalize_rel(value.strip())
    except UnsafePathError:
        return None
    if is_forbidden(norm):
        return None
    return norm


def inspect(config_dir: Path, scopes: tuple[str, ...], lovelace_fragment: str) -> BootstrapPlan:
    """Read-only analysis of ``configuration.yaml``; never modifies anything."""
    cfg_path = Path(config_dir) / "configuration.yaml"
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.error("Brak configuration.yaml w %s", config_dir)
        plan_ = BootstrapPlan(Layout.PARSE_ERROR)
        plan_.add_missing(MISSING_PARSE, INSTRUCTION_PARSE)
        plan_.lovelace_fragment = lovelace_fragment
        return plan_
    except (OSError, UnicodeDecodeError) as exc:
        log.error("Nie udało się odczytać configuration.yaml: %s", exc)
        plan_ = BootstrapPlan(Layout.PARSE_ERROR)
        plan_.add_missing(MISSING_PARSE, INSTRUCTION_PARSE)
        plan_.lovelace_fragment = lovelace_fragment
        return plan_

    has_marker = MARKER_BEGIN in text
    try:
        config = load_tolerant(text)
    except yaml.YAMLError as exc:
        log.error("configuration.yaml nie parsuje się: %s", str(exc).splitlines()[0])
        plan_ = BootstrapPlan(Layout.OK if has_marker else Layout.PARSE_ERROR)
        plan_.include_prefix = _include_prefix(plan_.package_path)
        if not has_marker:
            plan_.add_missing(MISSING_PARSE, INSTRUCTION_PARSE)
        plan_.lovelace_fragment = lovelace_fragment
        plan_.warnings.append("configuration.yaml nie parsuje się — pominięto sprawdzenie kolizji")
        return plan_

    if config is None:
        config = {}
    if not isinstance(config, dict):
        plan_ = BootstrapPlan(Layout.PARSE_ERROR)
        plan_.add_missing(MISSING_PARSE, INSTRUCTION_PARSE)
        plan_.lovelace_fragment = lovelace_fragment
        return plan_

    plan_ = _decide_layout(config, has_marker)
    plan_.include_prefix = _include_prefix(plan_.package_path)

    if "themes" in scopes and not _has_frontend_themes(config, plan_):
        plan_.add_missing(MISSING_THEMES, INSTRUCTION_THEMES)

    plan_.lovelace_fragment = _resolve_conflicts(config, lovelace_fragment, plan_)
    return plan_


def _decide_layout(config: dict, has_marker: bool) -> BootstrapPlan:
    if "homeassistant" not in config:
        if has_marker:
            return BootstrapPlan(Layout.OK)
        return BootstrapPlan(Layout.APPEND, append_block=True)

    ha = config.get("homeassistant")
    if ha is None:
        ha = {}
    if not isinstance(ha, dict):
        if has_marker:
            return BootstrapPlan(Layout.OK)
        plan_ = BootstrapPlan(Layout.MANUAL)
        plan_.add_missing(MISSING_PACKAGES, INSTRUCTION_PACKAGES_MANUAL)
        return plan_

    if "packages" not in ha or ha.get("packages") is None:
        if has_marker:
            return BootstrapPlan(Layout.OK)
        plan_ = BootstrapPlan(Layout.MANUAL)
        plan_.add_missing(MISSING_PACKAGES, INSTRUCTION_PACKAGES_UNDER_HOMEASSISTANT)
        return plan_

    packages = ha["packages"]
    if isinstance(packages, Tagged):
        if packages.tag in ("!include_dir_named", "!include_dir_merge_named"):
            pkg_dir = _safe_rel(packages.value)
            if pkg_dir is not None:
                plan_ = BootstrapPlan(Layout.INCLUDE_DIR)
                plan_.package_path = posixpath.join(pkg_dir, "home_labs.yaml")
                if packages.tag == "!include_dir_merge_named":
                    plan_.wrap_key = "home_labs"
                return plan_
        plan_ = BootstrapPlan(Layout.MANUAL)
        plan_.add_missing(MISSING_PACKAGES, INSTRUCTION_PACKAGES_MANUAL)
        return plan_

    if isinstance(packages, dict):
        entry = packages.get("home_labs")
        if entry is None:
            if has_marker:
                return BootstrapPlan(Layout.OK)
            plan_ = BootstrapPlan(Layout.MANUAL)
            plan_.add_missing(MISSING_PACKAGES, INSTRUCTION_PACKAGES_ENTRY)
            return plan_
        if isinstance(entry, Tagged) and entry.tag == "!include":
            rel = _safe_rel(entry.value)
            if rel is not None:
                plan_ = BootstrapPlan(Layout.OK)
                plan_.package_path = rel
                if rel != PACKAGE_PATH:
                    plan_.warnings.append(
                        f"homeassistant.packages.home_labs wskazuje na {rel} (oczekiwano "
                        f"{PACKAGE_PATH}) — używam tej ścieżki"
                    )
                return plan_
        plan_ = BootstrapPlan(Layout.MANUAL)
        plan_.add_missing(MISSING_PACKAGES, INSTRUCTION_PACKAGES_MANUAL)
        return plan_

    plan_ = BootstrapPlan(Layout.MANUAL)
    plan_.add_missing(MISSING_PACKAGES, INSTRUCTION_PACKAGES_MANUAL)
    return plan_


def _has_frontend_themes(config: dict, plan_: BootstrapPlan) -> bool:
    if "frontend" not in config:
        return False
    frontend = config.get("frontend")
    if frontend is None:
        return False
    if isinstance(frontend, Tagged):
        plan_.warnings.append(
            "frontend w configuration.yaml jest dołączany przez tag YAML — nie sprawdzono themes"
        )
        return True
    if isinstance(frontend, dict):
        return "themes" in frontend and frontend.get("themes") is not None
    return False


def _resolve_conflicts(config: dict, fragment: str, plan_: BootstrapPlan) -> str:
    """Drop ``lovelace`` keys already defined in configuration.yaml from our fragment."""
    if not fragment.strip():
        return ""
    conf_lovelace = config.get("lovelace")
    if conf_lovelace is None or "lovelace" not in config:
        return fragment
    if isinstance(conf_lovelace, Tagged):
        plan_.warnings.append(
            "lovelace w configuration.yaml jest dołączane przez tag YAML — nie sprawdzono kolizji"
        )
        return fragment
    if not isinstance(conf_lovelace, dict):
        return fragment
    try:
        data = load_tolerant(fragment)
    except yaml.YAMLError:
        return fragment
    if not isinstance(data, dict) or not isinstance(data.get("lovelace"), dict):
        return fragment
    frag_lovelace: dict = data["lovelace"]
    dropped = False
    for key in list(frag_lovelace):
        if key == "dashboards":
            frag_dash = frag_lovelace.get("dashboards")
            conf_dash = conf_lovelace.get("dashboards")
            if isinstance(frag_dash, dict) and isinstance(conf_dash, dict):
                for dash_key in list(frag_dash):
                    if dash_key in conf_dash:
                        plan_.conflicts.append(f"lovelace.dashboards.{dash_key}")
                        del frag_dash[dash_key]
                        dropped = True
                if not frag_dash:
                    del frag_lovelace["dashboards"]
            continue
        if key in conf_lovelace:
            plan_.conflicts.append(f"lovelace.{key}")
            del frag_lovelace[key]
            dropped = True
    if not dropped:
        return fragment
    if not frag_lovelace:
        return ""
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def append_block(config_dir: Path, backup_dir: Path) -> bool:
    """Back up configuration.yaml and append the marker block. Returns True when appended."""
    cfg_path = Path(config_dir) / "configuration.yaml"
    data = cfg_path.read_bytes()
    if MARKER_BEGIN.encode("utf-8") in data:
        return False
    backup_file(cfg_path, backup_dir, "configuration.yaml")
    with open(cfg_path, "ab") as fh:
        if data and not data.endswith(b"\n"):
            fh.write(b"\n")
        fh.write(BOOTSTRAP_BLOCK.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    log.info("Dopisano blok home-labs-sync do configuration.yaml (kopia w %s)", backup_dir)
    return True
