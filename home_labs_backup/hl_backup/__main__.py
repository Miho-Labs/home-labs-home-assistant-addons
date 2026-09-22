"""Entry point: ``python3 -m hl_backup``.

Every start (the Supervisor restarts the add-on when its options change) and
then every few hours: check the token, install/refresh the integration,
announce {api_url, api_token} to it through discovery. The backups themselves
are HA's job — this process only keeps the integration in place and configured.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import aiohttp

from . import VERSION, installer
from .options import (
    Options,
    Paths,
    load_options,
    resolve_paths,
    supervisor_token,
    supervisor_url,
)
from .supervisor import SupervisorApi, check_token

log = logging.getLogger("hl_backup")

INTERVAL = 6 * 3600
NOTIFY_RESTART = "home_labs_backup_restart"
NOTIFY_TOKEN = "home_labs_backup_token"


async def run_once(
    opts: Options, paths: Paths, session: aiohttp.ClientSession, sup: SupervisorApi
) -> dict:
    """One pass. Returns what happened (for logs and tests)."""
    result: dict = {"token": None, "install": None, "discovery": None}
    if not opts.api_token:
        log.error("Brak tokenu — wklej token z panelu Home Labs w konfiguracji dodatku")
        await sup.notify(
            NOTIFY_TOKEN,
            "Home Labs Backup: brak tokenu",
            "Wklej token z panelu Home Labs (zaczyna się od „hls_”) w zakładce Konfiguracja "
            "dodatku Home Labs Backup i uruchom go ponownie.",
        )
        result["token"] = "missing"
        return result

    outcome, message = await check_token(session, opts.api_url, opts.api_token)
    result["token"] = outcome
    if outcome == "auth":
        log.error("Token: %s", message)
        await sup.notify(
            NOTIFY_TOKEN,
            "Home Labs Backup: token odrzucony",
            "Serwer Home Labs nie przyjął tokenu. Wygeneruj nowy w panelu Home Labs, wklej go "
            "w konfiguracji dodatku Home Labs Backup i uruchom dodatek ponownie.",
        )
    else:
        (log.info if outcome == "ok" else log.warning)("Home Labs: %s", message)
        await sup.dismiss(NOTIFY_TOKEN)

    # Installed even with a bad token: the integration then shows the problem
    # where the owner looks for backups, and a fixed token needs no restart.
    result["install"] = installer.install(paths.integration_src, paths.integration_dst)
    if result["install"] != "unchanged":
        await sup.notify(
            NOTIFY_RESTART,
            "Home Labs Backup: uruchom ponownie Home Assistant",
            "Zainstalowano integrację kopii zapasowych Home Labs. Po ponownym uruchomieniu "
            "Home Assistant w Ustawienia → System → Kopie zapasowe pojawi się lokalizacja "
            "„Home Labs (chmura)” — zaznacz ją w ustawieniach automatycznych kopii.",
        )

    # The Supervisor keeps one message per add-on and service: the same config
    # again is a no-op, a new token updates it in place and HA re-runs the
    # flow, which updates the entry. Sent every pass, it also survives a
    # Supervisor restore that dropped it.
    discovery_uuid = await sup.send_discovery(
        {"api_url": opts.api_url, "api_token": opts.api_token}
    )
    result["discovery"] = "sent" if discovery_uuid else "skipped"
    return result


async def main() -> None:
    paths = resolve_paths()
    opts = load_options(paths.options_file)
    logging.basicConfig(
        level=getattr(logging, opts.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("Home Labs Backup %s, konfiguracja HA: %s", VERSION, paths.config_dir)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async with aiohttp.ClientSession() as session:
        sup = SupervisorApi(session, supervisor_url(), supervisor_token())
        while not stop.is_set():
            try:
                await run_once(opts, paths, session, sup)
            except Exception:
                log.exception("Przebieg nie powiódł się — spróbuję ponownie później")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=INTERVAL)
    log.info("Home Labs Backup zatrzymany")


if __name__ == "__main__":
    asyncio.run(main())
