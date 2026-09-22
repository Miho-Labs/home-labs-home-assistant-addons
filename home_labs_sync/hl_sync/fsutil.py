"""Filesystem helpers with the safety rules of the add-on baked in."""

from __future__ import annotations

import contextlib
import hashlib
import os
import posixpath
import shutil
from pathlib import Path

FORBIDDEN_FIRST_SEGMENTS = {".storage", ".cloud", "deps", "tts", "blueprints"}
FORBIDDEN_NAMES = {"secrets.yaml", "known_devices.yaml"}


class UnsafePathError(Exception):
    """A destination would land outside the config dir or on a protected file."""


def normalize_rel(rel: str) -> str:
    """Normalize a relative POSIX path; raise ``UnsafePathError`` when it escapes or is absolute."""
    if not isinstance(rel, str) or not rel or "\\" in rel or "\x00" in rel:
        raise UnsafePathError(f"nieprawidłowa ścieżka {rel!r}")
    norm = posixpath.normpath(rel)
    if norm.startswith("/") or norm == "." or norm.startswith("../") or norm == "..":
        raise UnsafePathError(f"ścieżka {rel!r} wychodzi poza katalog konfiguracji")
    return norm


def is_forbidden(rel: str) -> bool:
    """True for files the add-on must never touch (secrets, .storage, ...)."""
    norm = posixpath.normpath(rel)
    first = norm.split("/", 1)[0]
    return first in FORBIDDEN_FIRST_SEGMENTS or posixpath.basename(norm) in FORBIDDEN_NAMES


def resolve_inside(base_dir: Path, rel: str, what: str = "katalog konfiguracji") -> Path:
    """Absolute path for ``rel``; checks (via realpath) that it stays inside ``base_dir``.

    ``base_dir`` is the config dir or the media dir; ``what`` names it in error messages.
    """
    norm = normalize_rel(rel)
    if is_forbidden(norm):
        raise UnsafePathError(f"ścieżka {rel!r} jest chroniona")
    base = Path(os.path.realpath(base_dir))
    dest = Path(os.path.realpath(base / norm))
    if dest == base or base not in dest.parents:
        raise UnsafePathError(f"ścieżka {rel!r} wychodzi poza {what}")
    return dest


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    """sha256 of a regular file, or ``None`` when it does not exist / is not a file."""
    try:
        with open(path, "rb") as fh:
            digest = hashlib.sha256()
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None
    return digest.hexdigest()


def atomic_write(dest: Path, data: bytes) -> None:
    """Write ``data`` to ``dest`` via a temp file in the same directory + ``os.replace``."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.hl-sync-tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if dest.exists():
            with contextlib.suppress(OSError):
                shutil.copymode(dest, tmp)
        os.replace(tmp, dest)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def backup_file(src: Path, backup_dir: Path, rel: str) -> Path | None:
    """Copy ``src`` to ``backup_dir/rel`` (if ``src`` exists). Returns the backup path."""
    src = Path(src)
    if not src.is_file():
        return None
    target = Path(backup_dir) / normalize_rel(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    return target


def prune_dirs(root: Path, keep: int) -> None:
    """Keep only the ``keep`` most recently modified sub-directories of ``root``."""
    root = Path(root)
    if not root.is_dir():
        return
    dirs = [p for p in root.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dirs[keep:]:
        shutil.rmtree(old, ignore_errors=True)
