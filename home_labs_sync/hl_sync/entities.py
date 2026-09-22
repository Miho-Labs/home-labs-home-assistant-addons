"""Entity export: render the CSV template inside Home Assistant and upload it."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .client import AdminApiError, AdminClient
from .hass import HassApi, HassError

log = logging.getLogger(__name__)

HEADER = "entity_id;name;area;floor;device_class;unit;state"
TRIGGERS = ("panel", "schedule", "requested")
_TEMPLATE_FILE = Path(__file__).parent / "templates" / "export_entities.jinja"


def bundled_template() -> str:
    return _TEMPLATE_FILE.read_text(encoding="utf-8")


@dataclass
class ExportResult:
    trigger: str
    at: datetime
    ok: bool
    rows: int = 0
    message: str = ""
    export_id: str | None = None


async def run_export(
    hass: HassApi, client: AdminClient, template: str, trigger: str, now: datetime
) -> ExportResult:
    """Render the template via the Core API, validate the header, POST the CSV.

    Never touches the disk.
    """
    if trigger not in TRIGGERS:
        trigger = "panel"
    try:
        rendered = await hass.render_template(template)
    except HassError as exc:
        log.error("Eksport encji: %s", exc)
        return ExportResult(
            trigger, now, False, message=f"Renderowanie szablonu nie powiodło się: {exc}"
        )
    text = rendered.replace("\r\n", "\n").lstrip("\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != HEADER:
        got = (lines[0] if lines else "")[:120]
        log.error("Eksport encji: zły nagłówek CSV: %r", got)
        return ExportResult(trigger, now, False, message=f"Nieprawidłowy nagłówek CSV: {got!r}")
    lines[0] = HEADER
    csv_text = "\n".join(lines)
    if not csv_text.endswith("\n"):
        csv_text += "\n"
    rows = sum(1 for line in lines[1:] if line.strip())
    try:
        response = await client.post_entities(csv_text, trigger)
    except AdminApiError as exc:
        log.error("Eksport encji: serwer odrzucił wysyłkę: %s", exc)
        return ExportResult(
            trigger, now, False, rows=rows, message=f"Wysyłka nie powiodła się: {exc}"
        )
    export_id = response.get("export_id") if isinstance(response, dict) else None
    log.info("Wyeksportowano %d encji (%s), id eksportu: %s", rows, trigger, export_id)
    return ExportResult(
        trigger,
        now,
        True,
        rows=rows,
        message=f"Wysłano {rows} encji",
        export_id=str(export_id) if export_id else None,
    )


def schedule_due(schedule: str, hour: int, last_export: datetime | None, now: datetime) -> bool:
    """True when a scheduled export is due (``daily`` at ``hour``, ``weekly`` on Mondays)."""
    if schedule not in ("daily", "weekly"):
        return False
    due_at = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if schedule == "weekly":
        due_at -= timedelta(days=now.weekday())
    if due_at > now:
        due_at -= timedelta(days=1 if schedule == "daily" else 7)
    if last_export is None:
        return True
    if last_export.tzinfo is None and due_at.tzinfo is not None:
        last_export = last_export.replace(tzinfo=due_at.tzinfo)
    if last_export.tzinfo is not None and due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=last_export.tzinfo)
    return last_export < due_at
