"""Build the JSON body for ``POST /sync/v1/report``."""

from __future__ import annotations

from typing import Any

MAX_STR = 2000
MAX_LIST = 500
REPORT_STATUSES = ("ok", "noop", "error", "partial")


def clip(text: Any, limit: int = MAX_STR) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def clip_list(items: Any, limit: int = MAX_LIST) -> list[str]:
    if not items:
        return []
    return [clip(i) for i in list(items)[:limit]]


def build_report(result: Any, addon_version: str, ha_version: str) -> dict[str, Any]:
    """``result`` is a ``SyncResult``; only fields required by the contract are emitted."""
    status = result.status if result.status in REPORT_STATUSES else "error"
    return {
        "addon_version": clip(addon_version, 50),
        "ha_version": clip(ha_version, 50),
        "release_id": clip(result.release_id, 200) if result.release_id else None,
        "status": status,
        "applied": clip_list(result.applied),
        "deleted": clip_list(result.deleted),
        "errors": clip_list(result.errors),
        "needs_restart": bool(result.needs_restart),
        "restart_done": bool(result.restart_done),
        "check_result": result.check_result
        if result.check_result in ("ok", "error", "skipped")
        else "skipped",
        "check_output": clip(result.check_output),
        "bootstrap_missing": clip_list(result.bootstrap_missing),
        "bootstrap_conflict": clip_list(result.bootstrap_conflict),
        "scopes": clip_list(result.scopes),
        "dry_run": bool(result.dry_run),
        "duration_ms": int(result.duration_ms),
    }
