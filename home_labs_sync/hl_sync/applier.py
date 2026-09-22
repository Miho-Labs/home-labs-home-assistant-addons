"""Staging, applying release files and composing the ``home_labs`` package."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .client import AdminApiError, AdminClient
from .fsutil import (
    UnsafePathError,
    atomic_write,
    backup_file,
    normalize_rel,
    prune_dirs,
    resolve_inside,
)
from .hlyaml import load_tolerant
from .manifest import (
    AUTOMATIONS_PATH,
    MAX_FILE_SIZE,
    MAX_RELEASE_SIZE,
    ROOT_CONFIG,
    SCENES_PATH,
    FileEntry,
    Release,
    split_applied_key,
)
from .planner import Plan, display_key, resolve_managed

log = logging.getLogger(__name__)

PACKAGE_HEADER = "# home_labs package — zarządzane przez dodatek Home Labs Sync, nie edytuj\n"


class ApplyError(Exception):
    """Staging failed; nothing was written to the config dir."""


async def stage_release(
    client: AdminClient, writes: list[FileEntry], stage_dir: Path
) -> dict[str, Path]:
    """Download every blob into ``stage_dir`` (named by sha) and verify it. All or nothing.

    The result maps ``FileEntry.key`` (``root:path``) to the staged file.
    """
    stage_dir = Path(stage_dir)
    staged: dict[str, Path] = {}
    total = 0
    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
        for entry in writes:
            target = stage_dir / entry.sha256
            if not target.is_file() or target.stat().st_size != entry.size:
                try:
                    data = await client.get_file(entry.sha256, MAX_FILE_SIZE)
                except AdminApiError as exc:
                    raise ApplyError(f"pobieranie {entry.path}: {exc}") from exc
                if len(data) != entry.size:
                    raise ApplyError(
                        f"pobieranie {entry.path}: rozmiar {len(data)} B ≠ {entry.size} B "
                        "z manifestu"
                    )
                target.write_bytes(data)
            total += target.stat().st_size
            if total > MAX_RELEASE_SIZE:
                raise ApplyError(f"wydanie przekracza limit {MAX_RELEASE_SIZE} B")
            staged[entry.key] = target
    except OSError as exc:
        raise ApplyError(f"zapis do katalogu tymczasowego: {exc}") from exc
    except ApplyError:
        cleanup_stage(stage_dir)
        raise
    return staged


def cleanup_stage(stage_dir: Path) -> None:
    shutil.rmtree(stage_dir, ignore_errors=True)


@dataclass
class ApplyOutcome:
    """``applied`` / ``deleted`` hold ``root:path`` keys (like ``state.applied``)."""

    applied: list[str]
    deleted: list[str]
    errors: list[str]


def backup_rel(root: str, path: str) -> str:
    """Where a file goes inside ``/data/backup/<release>/``: config files keep their path,
    media files sit under ``media/`` (no config path starts with ``media/``)."""
    return path if root == ROOT_CONFIG else f"{root}/{path}"


def apply_plan(
    roots: Mapping[str, Path],
    plan_: Plan,
    staged: dict[str, Path],
    backup_dir: Path,
    previously_managed: set[str],
) -> ApplyOutcome:
    """Move staged files into their root dirs and remove obsolete managed files.

    ``roots`` maps a manifest ``root`` to its base dir; ``staged`` and ``previously_managed``
    use ``root:path`` keys.
    """
    outcome = ApplyOutcome([], [], [])
    for entry in plan_.writes:
        name = entry.display
        try:
            dest = resolve_managed(roots, entry.root, entry.path)
            data = staged[entry.key].read_bytes()
            if dest.exists() and entry.key not in previously_managed:
                backup_file(dest, backup_dir, backup_rel(entry.root, entry.path))
                log.info("Plik %s istniał już u klienta — kopia w %s", name, backup_dir)
            atomic_write(dest, data)
            outcome.applied.append(entry.key)
            log.info("Zapisano %s", name)
        except (OSError, UnsafePathError, KeyError) as exc:
            outcome.errors.append(f"nie udało się zapisać {name}: {exc}")
            log.error("Nie udało się zapisać %s: %s", name, exc)
    for key in plan_.deletes:
        root, path = split_applied_key(key)
        name = display_key(key)
        try:
            dest = resolve_managed(roots, root, path)
            if dest.is_file():
                backup_file(dest, backup_dir, backup_rel(root, path))
                dest.unlink()
                log.info("Usunięto %s (nie ma go już w wydaniu)", name)
            outcome.deleted.append(key)
        except (OSError, UnsafePathError) as exc:
            outcome.errors.append(f"nie udało się usunąć {name}: {exc}")
            log.error("Nie udało się usunąć %s: %s", name, exc)
    return outcome


def compose_package(release: Release, scopes: tuple[str, ...], layout: Any) -> str:
    """Text of the ``home_labs`` package for the enabled scopes.

    ``layout`` provides ``include_prefix`` (relative path from the package dir to ``home_labs/``)
    and ``wrap_key`` (set for ``!include_dir_merge_named`` layouts, where the file must be
    ``home_labs: {...}``).
    """
    files = release.files_by_path
    enabled = set(scopes)
    prefix = getattr(layout, "include_prefix", "") or ""
    body: list[str] = []
    if "dashboards" in enabled and release.lovelace_fragment.strip():
        body.append(release.lovelace_fragment.rstrip("\n") + "\n")
    if "automations" in enabled and AUTOMATIONS_PATH in files:
        body.append(f"automation home_labs: !include {prefix}automations.yaml\n")
    if "scenes" in enabled and SCENES_PATH in files:
        body.append(f"scene home_labs: !include {prefix}scenes.yaml\n")
    wrap_key = getattr(layout, "wrap_key", None)
    if wrap_key:
        if not body:
            return PACKAGE_HEADER + f"{wrap_key}: {{}}\n"
        indented = "".join(
            "".join(("  " + line if line.strip() else line) + "\n" for line in part.splitlines())
            for part in body
        )
        return PACKAGE_HEADER + f"{wrap_key}:\n" + indented
    return PACKAGE_HEADER + "".join(body)


@dataclass
class PackageDiff:
    path: str
    changed: bool
    lovelace_changed: bool
    exists: bool


def extract_lovelace(text: str | None, wrap_key: str | None) -> Any:
    """The ``lovelace`` value of a package text (plain structure) or ``None``."""
    if not text:
        return None
    try:
        data = load_tolerant(text)
    except yaml.YAMLError:
        return None
    if wrap_key and isinstance(data, dict):
        data = data.get(wrap_key)
    if isinstance(data, dict):
        return data.get("lovelace")
    return None


def package_diff(config_dir: Path, layout: Any, text: str) -> PackageDiff:
    """Compare the composed package with the existing file without writing anything."""
    rel = normalize_rel(layout.package_path)
    dest = resolve_inside(config_dir, rel)
    old: str | None = None
    exists = dest.is_file()
    if exists:
        old = dest.read_text(encoding="utf-8", errors="replace")
    changed = old != text
    wrap_key = getattr(layout, "wrap_key", None)
    lovelace_changed = changed and extract_lovelace(old, wrap_key) != extract_lovelace(
        text, wrap_key
    )
    return PackageDiff(rel, changed, lovelace_changed, exists)


def write_package(config_dir: Path, layout: Any, text: str) -> PackageDiff:
    """Write the package only when its content changed."""
    diff = package_diff(config_dir, layout, text)
    if diff.changed:
        dest = resolve_inside(config_dir, diff.path)
        atomic_write(dest, text.encode("utf-8"))
        log.info(
            "Zapisano pakiet %s%s", diff.path, " (zmiana lovelace)" if diff.lovelace_changed else ""
        )
    return diff


def prune_backups(backup_root: Path, keep: int = 2) -> None:
    prune_dirs(backup_root, keep)
