"""Validation of the manifest served by ``GET /sync/v1/manifest``."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from .hlyaml import contains_tagged, load_tolerant
from .options import SCOPES

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_RELEASE_SIZE = 150 * 1024 * 1024
MAX_FILES = 2000

# Where a file lands. ``config`` is the HA config dir (/homeassistant), ``media`` is HA's media
# dir (/media). A manifest entry without ``root`` comes from an older server and means ``config``.
ROOT_CONFIG = "config"
ROOT_MEDIA = "media"
ROOTS = (ROOT_CONFIG, ROOT_MEDIA)
ROOT_LABELS = {ROOT_CONFIG: "katalog konfiguracji", ROOT_MEDIA: "katalog mediów"}

AUTOMATIONS_PATH = "home_labs/automations.yaml"
SCENES_PATH = "home_labs/scenes.yaml"
PACKAGE_PATH = "home_labs/package.yaml"
ALLOWED_PREFIXES = ("dashboards/", "www/", "themes/")
ALLOWED_EXACT = {AUTOMATIONS_PATH: "automations", SCENES_PATH: "scenes"}
WALLPANEL_DIR = "wallpanel"
MEDIA_PREFIX = WALLPANEL_DIR + "/"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class ManifestError(ValueError):
    """The manifest violates the contract; nothing from it may be used."""


@dataclass(frozen=True)
class FileEntry:
    path: str
    scope: str
    sha256: str
    size: int
    root: str = ROOT_CONFIG

    @property
    def key(self) -> str:
        """Unambiguous identity across roots; also the key in ``state.applied``."""
        return applied_key(self.root, self.path)

    @property
    def display(self) -> str:
        """How the file is named in reports, the panel and the log."""
        return display_path(self.root, self.path)


def applied_key(root: str, path: str) -> str:
    """``state.applied`` key: ``config:dashboards/dom.yaml``, ``media:wallpanel/las.jpg``."""
    return f"{root}:{path}"


def split_applied_key(key: str) -> tuple[str, str]:
    """Inverse of ``applied_key``. A key without a root prefix was written by add-on 0.1.x,
    which only knew the config dir, so it is a ``config`` path (no config path can start with
    ``config:`` or ``media:`` — see ``scope_for_path``)."""
    root, sep, path = key.partition(":")
    if sep and root in ROOTS:
        return root, path
    return ROOT_CONFIG, key


def display_path(root: str, path: str) -> str:
    """Path as shown in ``applied`` / ``deleted`` of the report and in the panel.

    Config files keep their bare relative path (the wire format of 0.1.x, so existing report
    consumers see no change); media files get a ``media:`` prefix (``media:wallpanel/las.jpg``),
    which cannot collide with any config path.
    """
    return path if root == ROOT_CONFIG else f"{root}:{path}"


@dataclass
class Release:
    release_id: str
    commit: str
    published_at: str
    files: list[FileEntry]
    lovelace_fragment: str = ""
    managed_automations: list[str] = field(default_factory=list)
    managed_scenes: list[str] = field(default_factory=list)
    templates: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def files_by_path(self) -> dict[str, FileEntry]:
        """Config-root files by path (media files live in another tree and never match here)."""
        return {f.path: f for f in self.files if f.root == ROOT_CONFIG}

    def managed_ids(self) -> dict[str, list[str]]:
        return {"automations": list(self.managed_automations), "scenes": list(self.managed_scenes)}


@dataclass
class Manifest:
    schema: int
    client: str
    paused: bool
    release: Release | None
    entities_export_requested: str | None = None


def scope_for_path(path: str, root: str = ROOT_CONFIG) -> str | None:
    """Scope a manifest path belongs to, or ``None`` when the path is outside the allowlist.

    Root ``media`` allows only ``wallpanel/**`` (WallPanel screensaver photos, scope ``media``).
    """
    if root == ROOT_MEDIA:
        return "media" if path.startswith(MEDIA_PREFIX) else None
    if root != ROOT_CONFIG:
        return None
    if path in ALLOWED_EXACT:
        return ALLOWED_EXACT[path]
    if path.startswith(("dashboards/", "www/")):
        return "dashboards"
    if path.startswith("themes/"):
        return "themes"
    return None


def scope_for_key(key: str) -> str | None:
    """Scope of a ``state.applied`` key (``root:path``), or ``None`` when outside the allowlist."""
    root, path = split_applied_key(key)
    return scope_for_path(path, root)


def validate_path(path: Any, root: str = ROOT_CONFIG) -> str:
    """Return ``path`` when it is a safe, allow-listed relative path; raise ``ManifestError``."""
    if not isinstance(path, str) or not path:
        raise ManifestError(f"nieprawidłowa ścieżka: {path!r}")
    if "\\" in path or "\x00" in path:
        raise ManifestError(f"niedozwolone znaki w ścieżce: {path!r}")
    if path.startswith("/") or path.endswith("/"):
        raise ManifestError(f"ścieżka musi być względna: {path!r}")
    segments = path.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        raise ManifestError(f"ścieżka nie jest znormalizowana: {path!r}")
    if posixpath.normpath(path) != path:
        raise ManifestError(f"ścieżka nie jest znormalizowana: {path!r}")
    if scope_for_path(path, root) is None:
        raise ManifestError(
            f"ścieżka poza dozwolonymi katalogami ({ROOT_LABELS.get(root, root)}): {path!r}"
        )
    return path


def _require(obj: dict, key: str, types: type | tuple[type, ...], where: str) -> Any:
    if key not in obj:
        raise ManifestError(f"brak pola '{key}' w {where}")
    value = obj[key]
    if not isinstance(value, types) or (isinstance(value, bool) and bool not in _as_tuple(types)):
        raise ManifestError(f"pole '{key}' w {where} ma zły typ ({type(value).__name__})")
    return value


def _as_tuple(types: type | tuple[type, ...]) -> tuple[type, ...]:
    return types if isinstance(types, tuple) else (types,)


def _str_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ManifestError(f"{where} musi być listą")
    out: list[str] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise ManifestError(f"{where} zawiera nieprawidłowy element {item!r}")
        out.append(str(item))
    return out


def _validate_lovelace_fragment(text: Any) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ManifestError("package.lovelace musi być tekstem YAML")
    if not text.strip():
        return ""
    try:
        data = load_tolerant(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"package.lovelace nie jest poprawnym YAML: {exc}") from exc
    if data is None:
        return ""
    if not isinstance(data, dict):
        raise ManifestError("package.lovelace musi być mapą YAML")
    extra = set(data) - {"lovelace"}
    if extra:
        raise ManifestError(f"package.lovelace zawiera niedozwolone klucze: {sorted(extra)}")
    if contains_tagged(data):
        raise ManifestError("package.lovelace nie może zawierać tagów YAML (!include itp.)")
    return text if text.endswith("\n") else text + "\n"


def _validate_files(raw_files: Any) -> list[FileEntry]:
    if not isinstance(raw_files, list):
        raise ManifestError("release.files musi być listą")
    if len(raw_files) > MAX_FILES:
        raise ManifestError(f"zbyt wiele plików w wydaniu ({len(raw_files)} > {MAX_FILES})")
    files: list[FileEntry] = []
    seen: set[str] = set()
    total = 0
    for i, raw in enumerate(raw_files):
        where = f"release.files[{i}]"
        if not isinstance(raw, dict):
            raise ManifestError(f"{where} musi być obiektem")
        root = raw.get("root", ROOT_CONFIG)
        if root is None:
            root = ROOT_CONFIG
        if not isinstance(root, str) or root not in ROOTS:
            raise ManifestError(f"{where}: nieznany katalog docelowy (root) {root!r}")
        path = validate_path(_require(raw, "path", str, where), root)
        scope = _require(raw, "scope", str, where)
        if scope not in SCOPES:
            raise ManifestError(f"{where}: nieznany zakres {scope!r}")
        expected_scope = scope_for_path(path, root)
        if scope != expected_scope:
            raise ManifestError(
                f"{where}: zakres {scope!r} nie pasuje do ścieżki {path!r} w {root} "
                f"({expected_scope!r})"
            )
        sha = _require(raw, "sha256", str, where).lower()
        if not _SHA256_RE.match(sha):
            raise ManifestError(f"{where}: nieprawidłowa suma sha256")
        size = _require(raw, "size", int, where)
        if size < 0 or size > MAX_FILE_SIZE:
            raise ManifestError(f"{where}: plik {path} ma {size} B (limit {MAX_FILE_SIZE} B)")
        entry = FileEntry(path=path, scope=scope, sha256=sha, size=size, root=root)
        if entry.key in seen:
            raise ManifestError(f"{where}: zduplikowana ścieżka {path!r} ({root})")
        seen.add(entry.key)
        total += size
        files.append(entry)
    if total > MAX_RELEASE_SIZE:
        raise ManifestError(f"wydanie ma {total} B (limit {MAX_RELEASE_SIZE} B)")
    return files


def _validate_release(raw: Any) -> Release | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ManifestError("release musi być obiektem lub null")
    release_id = _require(raw, "release_id", str, "release").strip()
    if not release_id or len(release_id) > 200:
        raise ManifestError("release.release_id jest puste lub zbyt długie")
    commit = _require(raw, "commit", str, "release")
    if not _COMMIT_RE.match(commit):
        raise ManifestError("release.commit nie jest 40-znakowym SHA")
    published_at = _require(raw, "published_at", str, "release")
    files = _validate_files(_require(raw, "files", list, "release"))

    package = raw.get("package") or {}
    if not isinstance(package, dict):
        raise ManifestError("release.package musi być obiektem")
    extra = set(package) - {"lovelace"}
    if extra:
        raise ManifestError(f"release.package zawiera niedozwolone klucze: {sorted(extra)}")
    lovelace = _validate_lovelace_fragment(package.get("lovelace"))

    managed = raw.get("managed_ids") or {}
    if not isinstance(managed, dict):
        raise ManifestError("release.managed_ids musi być obiektem")
    managed_automations = _str_list(managed.get("automations"), "managed_ids.automations")
    managed_scenes = _str_list(managed.get("scenes"), "managed_ids.scenes")

    templates_raw = raw.get("templates") or {}
    if not isinstance(templates_raw, dict):
        raise ManifestError("release.templates musi być obiektem")
    templates: dict[str, str] = {}
    for key, value in templates_raw.items():
        if value is None:
            continue
        if not isinstance(key, str) or not isinstance(value, str):
            raise ManifestError("release.templates musi mapować tekst na tekst")
        templates[key] = value

    warnings = [str(w) for w in (raw.get("warnings") or []) if isinstance(w, (str, int, float))]
    return Release(
        release_id=release_id,
        commit=commit.lower(),
        published_at=published_at,
        files=files,
        lovelace_fragment=lovelace,
        managed_automations=managed_automations,
        managed_scenes=managed_scenes,
        templates=templates,
        warnings=warnings,
    )


def validate_release(raw: Any) -> Release | None:
    """Validate a release dict on its own — used for the copy cached in the state file."""
    return _validate_release(raw)


def validate_manifest(data: Any) -> Manifest:
    """Validate the raw JSON manifest. Raises ``ManifestError`` on any violation."""
    if not isinstance(data, dict):
        raise ManifestError("manifest musi być obiektem JSON")
    schema = _require(data, "schema", int, "manifest")
    if schema != 1:
        raise ManifestError(f"nieobsługiwana wersja schematu manifestu: {schema}")
    client = _require(data, "client", str, "manifest")
    paused = _require(data, "paused", bool, "manifest")
    release = _validate_release(data.get("release"))
    requests = data.get("requests") or {}
    if not isinstance(requests, dict):
        raise ManifestError("requests musi być obiektem")
    export_req = requests.get("entities_export")
    if export_req is not None and not isinstance(export_req, str):
        raise ManifestError("requests.entities_export musi być tekstem lub null")
    return Manifest(
        schema=schema,
        client=client,
        paused=paused,
        release=release,
        entities_export_requested=export_req or None,
    )
