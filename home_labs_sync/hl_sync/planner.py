"""Compute which files to write and delete for a release."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .fsutil import UnsafePathError, resolve_inside, sha256_file
from .manifest import (
    ROOT_LABELS,
    FileEntry,
    Release,
    display_path,
    scope_for_key,
    split_applied_key,
)
from .state import State

log = logging.getLogger(__name__)


def resolve_managed(roots: Mapping[str, Path], root: str, path: str) -> Path:
    """Absolute destination of a managed file, contained (via realpath) in its root's base dir."""
    base = roots.get(root)
    if base is None:
        raise UnsafePathError(f"nieznany katalog docelowy {root!r} dla {path!r}")
    return resolve_inside(base, path, ROOT_LABELS.get(root, root))


def display_key(key: str) -> str:
    """Report/panel form of a ``state.applied`` key (see ``manifest.display_path``)."""
    return display_path(*split_applied_key(key))


@dataclass
class Plan:
    """Lists are keyed like ``state.applied`` (``root:path``); ``writes`` holds the entries."""

    desired: dict[str, FileEntry] = field(default_factory=dict)
    writes: list[FileEntry] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    skipped_deletes: list[str] = field(default_factory=list)
    vanished: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.writes and not self.deletes

    def summary_lines(self) -> list[str]:
        lines = [f"zapis: {f.display}" for f in self.writes]
        lines += [f"usunięcie: {display_key(k)}" for k in self.deletes]
        lines += [
            f"pominięte usunięcie (zmieniony lokalnie): {display_key(k)}"
            for k in self.skipped_deletes
        ]
        return lines


def plan(
    release: Release, scopes: tuple[str, ...], state: State, roots: Mapping[str, Path]
) -> Plan:
    """Desired files for enabled scopes vs. local content and previously applied paths.

    ``roots`` maps a manifest ``root`` (``config`` / ``media``) to its base directory.
    """
    result = Plan()
    enabled = set(scopes)
    for entry in release.files:
        if entry.scope not in enabled:
            continue
        result.desired[entry.key] = entry
        try:
            local = resolve_managed(roots, entry.root, entry.path)
        except UnsafePathError as exc:
            result.errors.append(str(exc))
            continue
        if sha256_file(local) == entry.sha256:
            result.unchanged.append(entry.key)
        else:
            result.writes.append(entry)

    for key, sha in state.applied.items():
        scope = scope_for_key(key)
        if scope is None or scope not in enabled or key in result.desired:
            continue
        try:
            local = resolve_managed(roots, *split_applied_key(key))
        except UnsafePathError:
            result.vanished.append(key)
            continue
        local_sha = sha256_file(local)
        if local_sha is None:
            result.vanished.append(key)
        elif local_sha == sha:
            result.deletes.append(key)
        else:
            log.warning(
                "Plik %s został zmieniony lokalnie — nie usuwam, przestaję nim zarządzać",
                display_key(key),
            )
            result.skipped_deletes.append(key)

    result.writes.sort(key=lambda f: f.key)
    result.deletes.sort()
    return result


def next_applied(
    state: State,
    plan_: Plan,
    scopes: tuple[str, ...],
    deleted: list[str],
    failed_writes: set[str],
) -> dict[str, str]:
    """State ``applied`` after this run: carry over disabled scopes, add desired, drop deleted.

    All arguments and the result use ``root:path`` keys.
    """
    enabled = set(scopes)
    result: dict[str, str] = {}
    for key, sha in state.applied.items():
        scope = scope_for_key(key)
        if scope is not None and scope not in enabled:
            result[key] = sha
    for key, entry in plan_.desired.items():
        if key not in failed_writes:
            result[key] = entry.sha256
        elif key in state.applied:
            result[key] = state.applied[key]
    for key in plan_.deletes:
        if key not in deleted and key in state.applied:
            result[key] = state.applied[key]
    return result
