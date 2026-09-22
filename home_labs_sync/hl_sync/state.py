"""Persistent add-on state (``/data/state.json``)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .manifest import applied_key, split_applied_key

log = logging.getLogger(__name__)


@dataclass
class State:
    etag: str | None = None
    release_id: str | None = None
    commit: str | None = None
    published_at: str | None = None
    applied: dict[str, str] = field(default_factory=dict)
    restart_done_for: str | None = None
    pending_restart: bool = False
    last_report_at: str | None = None
    last_entities_request: str | None = None
    last_entities_export_at: str | None = None
    consecutive_failures: int = 0
    last_status: str | None = None
    last_sync_at: str | None = None
    export_template: str | None = None
    notified_bootstrap: bool = False
    notified_conflict: bool = False
    # The last release the server sent (raw manifest JSON). A 304 means the server has
    # nothing new, not that the box is in order — every cycle reconciles files and the
    # package against this copy, so a file the customer deleted or a lovelace block they
    # removed from configuration.yaml is put right without waiting for a new release.
    release: dict | None = None

    _path: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path) -> State:
        path = Path(path)
        state = cls()
        state._path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return state
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Nie udało się odczytać stanu %s (%s) — zaczynam od zera", path, exc)
            return state
        if not isinstance(raw, dict):
            return state
        known = {f.name for f in fields(cls) if not f.name.startswith("_")}
        for key, value in raw.items():
            if key in known:
                setattr(state, key, value)
        if not isinstance(state.applied, dict):
            state.applied = {}
        # 0.1.x stored bare config paths; since 0.2.0 every key carries its root
        # (``config:...`` / ``media:...``), so an upgrade neither re-downloads nor deletes.
        state.applied = {
            applied_key(*split_applied_key(str(k))): str(v) for k, v in state.applied.items()
        }
        state.consecutive_failures = int(state.consecutive_failures or 0)
        state.pending_restart = bool(state.pending_restart)
        return state

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("_path", None)
        return data

    def save(self) -> None:
        if self._path is None:
            raise RuntimeError("State has no path")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path)
