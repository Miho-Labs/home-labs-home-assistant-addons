"""Copy the bundled integration into ``/config/custom_components/home_labs_backup``.

The only thing the add-on writes in the HA config directory. The copy is
compared by content, so an unchanged integration is left alone (and HA needs
no restart), and replaced in one rename, so HA never sees half a directory.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

IGNORED = {"__pycache__"}


def _files(root: Path) -> list[Path]:
    out = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and not (IGNORED & set(rel.parts)) and path.suffix != ".pyc":
            out.append(rel)
    return out


def tree_digest(root: Path) -> str | None:
    """sha256 over relative paths and contents; None when the directory is missing."""
    if not root.is_dir():
        return None
    h = hashlib.sha256()
    for rel in _files(root):
        h.update(rel.as_posix().encode() + b"\0")
        h.update((root / rel).read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def install(src: Path, dst: Path) -> str:
    """Make ``dst`` equal to ``src``. Returns ``installed``, ``updated`` or ``unchanged``."""
    if not (src / "manifest.json").is_file():
        raise FileNotFoundError(f"brak integracji w obrazie dodatku: {src}")
    before = tree_digest(dst)
    if before is not None and before == tree_digest(src):
        return "unchanged"
    parent = dst.parent
    parent.mkdir(parents=True, exist_ok=True)
    # Dot-names are not valid Python identifiers, so HA never loads the
    # staging or the outgoing copy as an integration.
    stage = parent / f".{dst.name}.new-{uuid.uuid4().hex[:8]}"
    old = parent / f".{dst.name}.old-{uuid.uuid4().hex[:8]}"
    shutil.copytree(src, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    try:
        if dst.exists():
            os.replace(dst, old)
        os.replace(stage, dst)
    except OSError:
        if old.exists() and not dst.exists():
            os.replace(old, dst)
        shutil.rmtree(stage, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)
    result = "installed" if before is None else "updated"
    log.info("Integracja home_labs_backup: %s (%s)", result, dst)
    return result
