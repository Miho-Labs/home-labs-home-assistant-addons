"""Entry point: ``python3 -m hl_sync``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time

import aiohttp

from . import VERSION, panel
from .client import AdminClient
from .hass import HassApi
from .logbuf import RingBufferHandler, setup_logging
from .options import (
    Options,
    Paths,
    load_options,
    resolve_paths,
    supervisor_token,
    supervisor_url,
)
from .sync import SyncRunner

log = logging.getLogger("hl_sync")

MAX_SLEEP = 3600.0


class App:
    """Shared state between the main loop and the ingress panel."""

    def __init__(
        self, runner: SyncRunner, ring: RingBufferHandler, interval_seconds: float
    ) -> None:
        self.runner = runner
        self._ring = ring
        self._interval = interval_seconds
        self.wake = asyncio.Event()
        self.stop = asyncio.Event()
        self.sync_requested = False
        self.export_requested = False
        self.next_sync_at = time.monotonic()

    def request_sync(self) -> None:
        self.sync_requested = True
        self.wake.set()

    def request_export(self) -> None:
        self.export_requested = True
        self.wake.set()

    def log_lines(self, count: int) -> list[str]:
        return self._ring.lines(count)

    def next_sync_in(self) -> int | None:
        return int(self.next_sync_at - time.monotonic())

    async def run_loop(self) -> None:
        first = True
        while not self.stop.is_set():
            try:
                await self._tick(first)
            except Exception:
                log.exception("Błąd w pętli głównej")
            first = False
            now = time.monotonic()
            timeout = max(1.0, min(self.next_sync_at - now, MAX_SLEEP))
            if self.sync_requested or self.export_requested:
                continue
            self.wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wait_any(), timeout=timeout)

    async def _wait_any(self) -> None:
        wake = asyncio.ensure_future(self.wake.wait())
        stop = asyncio.ensure_future(self.stop.wait())
        try:
            await asyncio.wait({wake, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (wake, stop):
                if not task.done():
                    task.cancel()

    async def _tick(self, first: bool) -> None:
        now = time.monotonic()
        if self.sync_requested or now >= self.next_sync_at:
            trigger = "panel" if self.sync_requested else ("startup" if first else "schedule")
            self.sync_requested = False
            self.next_sync_at = time.monotonic() + self._interval
            await self.runner.run_once(trigger)
            self.next_sync_at = time.monotonic() + self._interval
        if self.export_requested:
            self.export_requested = False
            await self.runner.export_entities("panel")
        elif self.runner.export_due():
            log.info(
                "Eksport encji według harmonogramu (%s)",
                self.runner.options.entities_export_schedule,
            )
            await self.runner.export_entities("schedule")


async def main_async() -> int:
    paths: Paths = resolve_paths()
    options: Options = load_options(paths.options_file)
    ring = setup_logging(options.log_level)
    log.info(
        "Home Labs Sync %s — katalog konfiguracji: %s, mediów: %s, dane: %s",
        VERSION,
        paths.config_dir,
        paths.media_dir,
        paths.data_dir,
    )
    log.info(
        "Serwer: %s, co %d min, zakresy: %s, restart: %s%s",
        options.api_url,
        options.interval_minutes,
        ", ".join(options.scopes) or "(brak)",
        options.restart_policy,
        " — TRYB PRÓBNY (dry_run)" if options.dry_run else "",
    )
    if not options.api_token:
        log.error("Brak tokenu API — wpisz token z panelu Home Labs w konfiguracji dodatku")
    token = supervisor_token()
    if not token:
        log.warning(
            "Brak SUPERVISOR_TOKEN — wywołania Home Assistant będą pomijane (tryb samodzielny)"
        )

    async with aiohttp.ClientSession() as session:
        client = AdminClient(session, options.api_url, options.api_token)
        hass = HassApi(session, supervisor_url(), token)
        runner = SyncRunner(options, paths, client, hass)
        app = App(runner, ring, options.interval_minutes * 60)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _on_signal, app, sig)

        web_runner = await panel.start_panel(app)
        log.info("Panel dostępny na porcie %d (ingress)", panel.PANEL_PORT)
        try:
            await app.run_loop()
        finally:
            await web_runner.cleanup()
    log.info("Home Labs Sync zatrzymany")
    return 0


def _on_signal(app: App, sig: signal.Signals) -> None:
    log.info("Otrzymano sygnał %s — zatrzymuję", sig.name)
    app.stop.set()
    app.wake.set()


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
