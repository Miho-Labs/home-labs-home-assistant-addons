"""Ingress panel: server-rendered status page with two action buttons."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Protocol

from aiohttp import web

from . import VERSION

PANEL_PORT = 8099

STATUS_LABELS = {
    "ok": ("OK", "ok"),
    "noop": ("bez zmian", "ok"),
    "paused": ("wstrzymana w panelu Home Labs", "warn"),
    "partial": ("częściowo — sprawdź błędy", "warn"),
    "error": ("błąd", "err"),
    "unreachable": ("serwer niedostępny", "warn"),
}
CHECK_LABELS = {"ok": "OK", "error": "błąd", "skipped": "pominięte (brak Supervisora)"}
TRIGGER_LABELS = {
    "schedule": "harmonogram",
    "panel": "przycisk w panelu",
    "requested": "prośba Home Labs",
    "startup": "start dodatku",
}


class PanelContext(Protocol):
    """What the panel needs from the running application."""

    runner: Any

    def request_sync(self) -> None: ...

    def request_export(self) -> None: ...

    def log_lines(self, count: int) -> list[str]: ...

    def next_sync_in(self) -> int | None: ...


CSS = """
:root{color-scheme:dark}
body{margin:0;padding:16px;background:#0b1b33;color:#e6edf7;
  font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
h1{font-size:20px;margin:0 0 12px}
h1 small{color:#8fa3c7;font-weight:400;font-size:13px;margin-left:8px}
h2{font-size:15px;margin:0 0 10px;color:#8fa3c7;text-transform:uppercase;letter-spacing:.04em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),1fr));
  gap:14px;margin-bottom:14px}
.card{background:#122a4d;border-radius:12px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.4);
  min-width:0}
table{border-collapse:collapse;width:100%;table-layout:fixed}
td{padding:4px 6px;vertical-align:top;border-bottom:1px solid #1d3b66;overflow-wrap:anywhere}
td:first-child{color:#8fa3c7;width:38%;overflow-wrap:normal}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:600;font-size:13px}
.ok{background:#1e7a4c;color:#fff}.warn{background:#a86b0b;color:#fff}.err{background:#a12b2b;color:#fff}
pre{background:#081428;color:#cfe0ff;padding:10px;border-radius:8px;overflow:auto;
  font-size:12.5px;margin:6px 0 0;white-space:pre-wrap}
form{display:inline-block;margin:0 8px 8px 0}
button{background:#4fb3ff;color:#06172e;border:0;border-radius:8px;padding:10px 16px;
  font-weight:600;font-size:14px;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
ul{margin:4px 0;padding-left:18px}.muted{color:#8fa3c7}
.note{background:#0f223f;border-left:3px solid #4fb3ff;padding:8px 10px;
  border-radius:6px;margin:8px 0}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _fmt_dt(value: datetime | str | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value).replace("T", " ")[:19]


def _row(label: str, value_html: str) -> str:
    return f"<tr><td>{_e(label)}</td><td>{value_html}</td></tr>"


def render_page(ctx: PanelContext, prefix: str, flash: str | None = None) -> str:
    runner = ctx.runner
    result = runner.last_result
    state = runner.state
    options = runner.options
    parts: list[str] = []

    parts.append(f"<h1>Home Labs Sync <small>v{_e(VERSION)}</small></h1>")
    if flash:
        parts.append(f'<div class="note">{_e(flash)}</div>')
    if not options.api_token:
        parts.append(
            '<div class="note">Brak tokenu API. Wpisz token z panelu Home Labs '
            "w konfiguracji dodatku "
            "(Ustawienia → Dodatki → Home Labs Sync → Konfiguracja).</div>"
        )
    parts.append('<div class="grid">')

    # --- status card
    rows: list[str] = []
    if result is None:
        rows.append(_row("Stan", '<span class="badge warn">jeszcze nie synchronizowano</span>'))
    else:
        label, cls = STATUS_LABELS.get(result.status, (result.status, "warn"))
        if result.status == "unreachable" and result.consecutive_failures < 3:
            cls = "warn"
        elif result.status == "unreachable":
            cls = "err"
        if result.dry_run and result.status in ("ok", "noop"):
            label += " (tryb próbny — nic nie zapisano)"
        rows.append(_row("Stan", f'<span class="badge {cls}">{_e(label)}</span>'))
    release_id = (result.release_id if result else None) or state.release_id
    commit = (result.commit if result else None) or state.commit
    published = (result.published_at if result else None) or state.published_at
    rows.append(_row("Wydanie", _e(release_id or "—")))
    rows.append(_row("Commit", f"<code>{_e((commit or '')[:8] or '—')}</code>"))
    rows.append(_row("Opublikowano", _e(_fmt_dt(published))))
    if result is not None:
        trig = TRIGGER_LABELS.get(result.trigger, result.trigger)
        rows.append(
            _row(
                "Ostatnia synchronizacja",
                f'{_e(_fmt_dt(result.started_at))} <span class="muted">'
                f"({_e(trig)}, {result.duration_ms} ms)</span>",
            )
        )
        if result.applied:
            rows.append(
                _row(
                    "Zapisane pliki",
                    "<ul>"
                    + "".join(f"<li><code>{_e(p)}</code></li>" for p in result.applied[:30])
                    + "</ul>",
                )
            )
        if result.deleted:
            rows.append(
                _row(
                    "Usunięte pliki",
                    "<ul>"
                    + "".join(f"<li><code>{_e(p)}</code></li>" for p in result.deleted[:30])
                    + "</ul>",
                )
            )
        if result.dry_run and result.plan_lines:
            rows.append(
                _row(
                    "Plan (tryb próbny)",
                    "<ul>" + "".join(f"<li>{_e(p)}</li>" for p in result.plan_lines[:40]) + "</ul>",
                )
            )
        if result.errors:
            rows.append(
                _row(
                    "Błędy",
                    "<ul>" + "".join(f"<li>{_e(e)}</li>" for e in result.errors[:20]) + "</ul>",
                )
            )
        if result.warnings:
            rows.append(
                _row(
                    "Ostrzeżenia",
                    "<ul>" + "".join(f"<li>{_e(w)}</li>" for w in result.warnings[:20]) + "</ul>",
                )
            )
        check = CHECK_LABELS.get(result.check_result, result.check_result)
        check_html = _e(check)
        if result.check_output:
            check_html += f"<pre>{_e(result.check_output[:1500])}</pre>"
        rows.append(_row("Sprawdzenie konfiguracji", check_html))
        if result.bootstrap_missing:
            items = []
            for key in result.bootstrap_missing:
                instr = result.bootstrap_instructions.get(key, "")
                items.append(f"<li><b>{_e(key)}</b><pre>{_e(instr)}</pre></li>")
            rows.append(
                _row(
                    "Bootstrap configuration.yaml",
                    '<span class="badge warn">do uzupełnienia ręcznie</span><ul>'
                    + "".join(items)
                    + "</ul>",
                )
            )
        else:
            rows.append(_row("Bootstrap configuration.yaml", '<span class="badge ok">OK</span>'))
        if result.bootstrap_conflict:
            rows.append(
                _row(
                    "Kolizje",
                    '<span class="badge warn">'
                    + _e(", ".join(result.bootstrap_conflict))
                    + '</span><div class="muted">Usuń stary blok <code>lovelace:</code> '
                    "z configuration.yaml — zarządza nim teraz Home Labs Sync.</div>",
                )
            )
    needs_restart = (result.needs_restart if result else False) or state.pending_restart
    if needs_restart:
        rows.append(
            _row("Restart", '<span class="badge warn">wymagany restart Home Assistant</span>')
        )
    else:
        rows.append(_row("Restart", '<span class="muted">nie jest wymagany</span>'))
    nxt = ctx.next_sync_in()
    if nxt is not None:
        rows.append(_row("Następna synchronizacja", f"za {max(0, nxt) // 60} min"))
    parts.append('<div class="card"><h2>Status</h2><table>' + "".join(rows) + "</table></div>")

    # --- actions + settings card
    busy = runner.running or runner.exporting
    disabled = " disabled" if busy else ""
    actions = (
        f'<form method="post" action="{_e(prefix)}/action/sync">'
        f'<button type="submit"{disabled}>Synchronizuj teraz</button></form>'
        f'<form method="post" action="{_e(prefix)}/action/export-entities">'
        f'<button type="submit"{disabled}>Eksportuj encje teraz</button></form>'
    )
    if busy:
        actions += '<div class="muted">Trwa operacja… odśwież stronę za chwilę.</div>'
    settings = [
        _row("Serwer", _e(options.api_url)),
        _row("Co ile", f"{options.interval_minutes} min"),
        _row("Zakresy", _e(", ".join(options.scopes) or "—")),
        _row(
            "Polityka restartu",
            _e(options.restart_policy)
            + (f" ({_e(options.restart_window)})" if options.restart_policy == "auto" else ""),
        ),
        _row(
            "Eksport encji",
            _e(options.entities_export_schedule)
            + (
                f", godz. {options.entities_export_hour}:00"
                if options.entities_export_schedule != "off"
                else ""
            ),
        ),
        _row("Tryb próbny", "tak" if options.dry_run else "nie"),
    ]
    export = runner.last_export
    if export is None:
        export_html = '<span class="muted">jeszcze nie eksportowano</span>'
    else:
        cls = "ok" if export.ok else "err"
        trig = TRIGGER_LABELS.get(export.trigger, export.trigger)
        export_html = (
            f'<span class="badge {cls}">{"OK" if export.ok else "błąd"}</span> '
            f'{_e(_fmt_dt(export.at))} <span class="muted">({_e(trig)})</span>'
            f"<div>{_e(export.message)}</div>"
        )
        if export.export_id:
            export_html += (
                f'<div class="muted">id eksportu: <code>{_e(export.export_id)}</code></div>'
            )
    parts.append(
        '<div class="card"><h2>Akcje</h2>'
        + actions
        + '<h2 style="margin-top:14px">Ostatni eksport encji</h2>'
        + export_html
        + '<h2 style="margin-top:14px">Ustawienia</h2><table>'
        + "".join(settings)
        + "</table></div>"
    )

    parts.append("</div>")

    # --- log card (outside the grid: a full-width item would keep every auto-fit
    # track alive and squeeze the cards above into narrow columns)
    lines = ctx.log_lines(20)
    parts.append(
        '<div class="card"><h2>Ostatnie wpisy dziennika</h2><pre>'
        + _e("\n".join(lines) or "(pusto)")
        + "</pre></div>"
    )

    body = "".join(parts)
    return (
        '<!doctype html><html lang="pl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Home Labs Sync</title><style>{CSS}</style></head><body>{body}</body></html>"
    )


def _prefix(request: web.Request) -> str:
    return request.headers.get("X-Ingress-Path", "").rstrip("/")


def create_app(ctx: PanelContext) -> web.Application:
    app = web.Application()

    async def index(request: web.Request) -> web.Response:
        flash = request.query.get("msg")
        return web.Response(
            text=render_page(ctx, _prefix(request), flash),
            content_type="text/html",
            charset="utf-8",
        )

    async def action_sync(request: web.Request) -> web.Response:
        ctx.request_sync()
        raise web.HTTPSeeOther(f"{_prefix(request)}/?msg=Zlecono+synchronizacj%C4%99")

    async def action_export(request: web.Request) -> web.Response:
        ctx.request_export()
        raise web.HTTPSeeOther(f"{_prefix(request)}/?msg=Zlecono+eksport+encji")

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "version": VERSION})

    app.router.add_get("/", index)
    app.router.add_post("/action/sync", action_sync)
    app.router.add_post("/action/export-entities", action_export)
    app.router.add_get("/health", health)
    return app


async def start_panel(
    ctx: PanelContext, host: str = "0.0.0.0", port: int = PANEL_PORT
) -> web.AppRunner:
    runner = web.AppRunner(create_app(ctx), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
