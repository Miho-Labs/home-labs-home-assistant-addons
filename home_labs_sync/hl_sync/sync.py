"""One synchronization run: manifest -> plan -> stage -> apply -> package -> takeover -> report."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from . import VERSION, applier, bootstrap, entities, planner, takeover
from .client import AdminApiError, AdminClient, AuthError
from .entities import ExportResult
from .hass import SENSOR_ENTITY_ID, HassApi, HassError
from .manifest import (
    WALLPANEL_DIR,
    Manifest,
    ManifestError,
    Release,
    scope_for_key,
    validate_manifest,
    validate_release,
)
from .options import Options, Paths, in_window
from .report import build_report, clip
from .state import State

log = logging.getLogger(__name__)

NOTIFY_RESTART = "home_labs_sync_restart"
NOTIFY_BOOTSTRAP = "home_labs_sync_bootstrap"
NOTIFY_CONFLICT = "home_labs_sync_conflict"

RESTART_TITLE = "Home Labs Sync: wymagany restart Home Assistant"
PACKAGES_SCOPE_OFF = (
    "Wydanie zawiera pakiety Home Labs (clients/<slug>/packages/), ale zakres 'packages' "
    "jest wyłączony w opcjach dodatku — pakiety pominięte. Włącz go w Konfiguracji dodatku."
)
RESTART_MESSAGE = (
    "Dodatek Home Labs Sync zaktualizował konfigurację dashboardów (wydanie {commit}). "
    "Aby zmiany były widoczne, uruchom ponownie Home Assistant: "
    "Ustawienia → System → ⋮ (prawy górny róg) → Uruchom ponownie Home Assistant."
)
BOOTSTRAP_TITLE = "Home Labs Sync: dokończ konfigurację"
BOOTSTRAP_INTRO = (
    "Dodatek Home Labs Sync nie może sam dokończyć konfiguracji. "
    "Uzupełnij plik configuration.yaml, a potem uruchom ponownie Home Assistant.\n\n"
)
CONFLICT_TITLE = "Home Labs Sync: kolizja w configuration.yaml"
CONFLICT_MESSAGE = (
    "W configuration.yaml jest już blok `lovelace:` z dashboardami: {keys}. "
    "Home Labs Sync zarządza teraz tymi dashboardami (plik {package}). "
    "Usuń stary blok `lovelace:` z configuration.yaml, a potem uruchom ponownie Home Assistant."
)

MEDIA_DIR_MISSING = (
    "Katalog mediów {dir} nie istnieje (brak montowania /media — starszy Supervisor?). "
    "Pomijam zakres media (zdjęcia wygaszacza WallPanel); pozostałe zakresy synchronizuję. "
    "Zaktualizuj Home Assistant albo wyłącz zakres media w konfiguracji dodatku."
)
MEDIA_DIR_UNWRITABLE = "nie udało się utworzyć katalogu {dir}: {exc} — pomijam zakres media"

REPORT_STALE_AFTER = timedelta(hours=24)
SENSOR_STATES = ("ok", "error", "noop", "paused", "dry_run")


def local_now() -> datetime:
    return datetime.now(UTC).astimezone()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass
class SyncResult:
    trigger: str
    started_at: datetime
    scopes: list[str]
    dry_run: bool = False
    status: str = "noop"  # ok|noop|error|partial|paused|unreachable
    not_modified: bool = False
    client_slug: str | None = None
    release_id: str | None = None
    commit: str | None = None
    published_at: str | None = None
    applied: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_restart: bool = False
    restart_done: bool = False
    check_result: str = "skipped"
    check_output: str = ""
    bootstrap_missing: list[str] = field(default_factory=list)
    bootstrap_instructions: dict[str, str] = field(default_factory=dict)
    bootstrap_conflict: list[str] = field(default_factory=list)
    plan_lines: list[str] = field(default_factory=list)
    duration_ms: int = 0
    report_wanted: bool = False
    report_sent: bool = False
    consecutive_failures: int = 0

    @property
    def sensor_state(self) -> str:
        if self.dry_run and self.status in ("ok", "noop"):
            return "dry_run"
        if self.status in ("ok", "noop", "paused"):
            return self.status
        return "error"

    @property
    def short_commit(self) -> str:
        return (self.commit or "")[:8]


class SyncRunner:
    def __init__(
        self,
        options: Options,
        paths: Paths,
        client: AdminClient,
        hass: HassApi,
        *,
        clock: Callable[[], datetime] | None = None,
        restart_poll_seconds: float = 5.0,
        restart_timeout: float = 300.0,
    ) -> None:
        self.options = options
        self.paths = paths
        self.client = client
        self.hass = hass
        self.state = State.load(paths.state_file)
        self.last_result: SyncResult | None = None
        self.last_export: ExportResult | None = None
        self.running = False
        self.exporting = False
        self._clock = clock or local_now
        self._restart_poll = restart_poll_seconds
        self._restart_timeout = restart_timeout
        self._lock = asyncio.Lock()

    def now(self) -> datetime:
        return self._clock()

    # ------------------------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------------------------

    async def run_once(self, trigger: str = "schedule") -> SyncResult:
        async with self._lock:
            self.running = True
            result = SyncResult(
                trigger=trigger,
                started_at=self.now(),
                scopes=list(self.options.scopes),
                dry_run=self.options.dry_run,
            )
            t0 = time.monotonic()
            log.info("Synchronizacja (%s) — start", trigger)
            try:
                await self._sync(result)
            except Exception as exc:
                log.exception("Nieoczekiwany błąd synchronizacji")
                result.status = "error"
                result.errors.append(f"nieoczekiwany błąd: {exc}")
                result.report_wanted = True
            result.duration_ms = int((time.monotonic() - t0) * 1000)
            result.consecutive_failures = self.state.consecutive_failures
            self.last_result = result
            await self._publish_sensor(result)
            if result.report_wanted:
                await self._send_report(result)
            if result.status != "unreachable":
                self.state.last_status = result.sensor_state
            self.state.last_sync_at = result.started_at.isoformat(timespec="seconds")
            self._save_state()
            log.info(
                "Synchronizacja — koniec: %s (%d ms)%s",
                result.status,
                result.duration_ms,
                "; " + "; ".join(result.errors) if result.errors else "",
            )
            self.running = False
            return result

    async def _sync(self, result: SyncResult) -> None:
        if not self.options.api_token:
            result.status = "error"
            result.errors.append(
                "Brak tokenu API. Wpisz token z panelu Home Labs w konfiguracji dodatku."
            )
            log.error(result.errors[-1])
            return

        try:
            # The panel button is a request for a full pass: skip the ETag so the server
            # answers 200 even when nothing changed on its side.
            etag = None if result.trigger == "panel" else self.state.etag
            resp = await self.client.get_manifest(etag=etag)
        except AuthError as exc:
            result.status = "error"
            result.errors.append(
                f"Serwer odrzucił token ({exc}). Sprawdź token w konfiguracji dodatku."
            )
            log.error(result.errors[-1])
            return
        except AdminApiError as exc:
            self.state.consecutive_failures += 1
            result.status = "unreachable"
            result.errors.append(f"Serwer Home Labs niedostępny: {exc}")
            level = logging.ERROR if self.state.consecutive_failures >= 3 else logging.WARNING
            log.log(
                level,
                "Serwer Home Labs niedostępny (%d. raz z rzędu): %s",
                self.state.consecutive_failures,
                exc,
            )
            return
        self.state.consecutive_failures = 0

        await self._detect_manual_restart()

        if resp.status == 304:
            result.not_modified = True
            cached = self._cached_release()
            if cached is None:
                result.status = "noop"
                result.release_id = self.state.release_id
                result.commit = self.state.commit
                result.published_at = self.state.published_at
                result.needs_restart = self.state.pending_restart
                log.info("Manifest bez zmian (304)")
                if self._report_is_stale():
                    result.report_wanted = True
                return
            log.info("Manifest bez zmian (304) — uzgadniam pliki i pakiet z ostatnim wydaniem")
            await self._apply_release(result, cached, self.state.etag, reconcile=True)
            return

        try:
            manifest = validate_manifest(resp.data)
        except ManifestError as exc:
            result.status = "error"
            result.errors.append(f"Nieprawidłowy manifest: {exc}")
            result.release_id = _guess_release_id(resp.data)
            result.report_wanted = True
            log.error("Nieprawidłowy manifest — nic nie zapisano: %s", exc)
            return

        result.client_slug = manifest.client
        new_etag = resp.etag

        if manifest.paused:
            result.status = "paused"
            log.info("Synchronizacja wstrzymana w panelu Home Labs")
            self.state.etag = new_etag
            return

        await self._handle_export_request(manifest)

        if manifest.release is None:
            result.status = "noop"
            result.report_wanted = True
            self.state.etag = new_etag
            self.state.release = None
            log.info("Brak opublikowanego wydania — nic do zrobienia")
            return

        raw_release = resp.data.get("release") if isinstance(resp.data, dict) else None
        self.state.release = raw_release if isinstance(raw_release, dict) else None
        await self._apply_release(result, manifest.release, new_etag)

    def _cached_release(self) -> Release | None:
        if not isinstance(self.state.release, dict):
            return None
        try:
            return validate_release(self.state.release)
        except ManifestError as exc:
            log.warning("Zapamiętane wydanie nie przechodzi walidacji (%s) — pomijam", exc)
            return None

    async def _apply_release(
        self,
        result: SyncResult,
        release: Release,
        new_etag: str | None,
        reconcile: bool = False,
    ) -> None:
        """Bring the box in line with ``release``.

        ``reconcile`` is the 304 path: the same work against the cached release, quiet when
        nothing needed doing (status ``noop``, no report unless the last one is stale).
        """
        result.release_id = release.release_id
        result.commit = release.commit
        result.published_at = release.published_at
        result.warnings.extend(release.warnings)
        for warning in release.warnings:
            log.log(
                logging.DEBUG if reconcile else logging.WARNING,
                "Ostrzeżenie z serwera: %s",
                warning,
            )
        if release.templates.get("entities_export"):
            self.state.export_template = release.templates["entities_export"]

        scopes = self.options.scopes
        if release.packages_fragment and "packages" not in scopes:
            result.warnings.append(PACKAGES_SCOPE_OFF)
            log.log(logging.DEBUG if reconcile else logging.WARNING, PACKAGES_SCOPE_OFF)
        if "media" in scopes and not self._media_ready(result):
            scopes = tuple(s for s in scopes if s != "media")
        config_dir = self.paths.config_dir

        plan_ = planner.plan(release, scopes, self.state, self.paths.roots)
        result.errors.extend(plan_.errors)
        fragment = release.lovelace_fragment if "dashboards" in scopes else ""
        bplan = bootstrap.inspect(config_dir, scopes, fragment)
        result.bootstrap_missing = list(bplan.missing)
        result.bootstrap_instructions = dict(bplan.instructions)
        result.bootstrap_conflict = list(bplan.conflicts)
        result.warnings.extend(bplan.warnings)
        for conflict in bplan.conflicts:
            log.warning("Kolizja z configuration.yaml: %s — pomijam ten klucz w pakiecie", conflict)

        trimmed = Release(**{**release.__dict__, "lovelace_fragment": bplan.lovelace_fragment})
        package_text = applier.compose_package(trimmed, scopes, bplan)
        pkg_diff = applier.package_diff(config_dir, bplan, package_text)

        result.plan_lines = plan_.summary_lines()
        if pkg_diff.changed:
            result.plan_lines.append(f"zapis pakietu: {pkg_diff.path}")
        if bplan.append_block:
            result.plan_lines.append("dopisanie bloku home-labs-sync do configuration.yaml")

        if self.options.dry_run:
            result.applied = [f.display for f in plan_.writes] + (
                [pkg_diff.path] if pkg_diff.changed else []
            )
            result.deleted = [planner.display_key(k) for k in plan_.deletes]
            result.needs_restart = (
                pkg_diff.lovelace_changed or pkg_diff.packages_changed or bplan.append_block
            )
            result.status = "ok" if result.plan_lines else "noop"
            result.report_wanted = True
            log.info("Tryb próbny (dry_run) — plan bez zapisu:")
            for line in result.plan_lines or ["(brak zmian)"]:
                log.info("  %s", line)
            return

        if plan_.errors:
            result.status = "error"
            result.report_wanted = True
            return

        stage_dir = self.paths.stage_root / release.release_id
        try:
            staged = await applier.stage_release(self.client, plan_.writes, stage_dir)
        except applier.ApplyError as exc:
            result.status = "error"
            result.errors.append(f"Pobieranie wydania nie powiodło się — nic nie zapisano: {exc}")
            result.report_wanted = True
            log.error(result.errors[-1])
            return

        backup_dir = self.paths.backup_root / release.release_id
        outcome = applier.apply_plan(
            self.paths.roots, plan_, staged, backup_dir, set(self.state.applied)
        )
        applier.cleanup_stage(stage_dir)
        # Reports and the panel show config files by bare path and media files as
        # ``media:wallpanel/...`` (see ``manifest.display_path``).
        result.applied.extend(planner.display_key(k) for k in outcome.applied)
        result.deleted.extend(planner.display_key(k) for k in outcome.deleted)
        result.errors.extend(outcome.errors)
        failed_writes = {f.key for f in plan_.writes} - set(outcome.applied)

        pkg = applier.write_package(config_dir, bplan, package_text)
        if pkg.changed:
            result.applied.append(pkg.path)

        appended = False
        if bplan.append_block:
            try:
                appended = bootstrap.append_block(config_dir, backup_dir)
            except OSError as exc:
                result.errors.append(f"nie udało się dopisać bloku do configuration.yaml: {exc}")
                log.error(result.errors[-1])

        tk = takeover.run(config_dir, backup_dir, release.managed_ids(), scopes)
        result.warnings.extend(tk.warnings)
        applier.prune_backups(self.paths.backup_root, keep=2)

        changed_scopes = {scope_for_key(k) for k in outcome.applied + outcome.deleted} - {None}
        changed_scopes |= tk.changed_scopes
        if pkg.changed:
            changed_scopes |= {"automations", "scenes"} & set(scopes)
        result.warnings.extend(await self.hass.reload_scopes(changed_scopes - {"dashboards"}))

        result.check_result, check_output = await self.hass.check_config()
        result.check_output = clip(check_output)
        if result.check_result == "error":
            log.error("Sprawdzenie konfiguracji Home Assistant: błąd: %s", result.check_output)

        # New or changed integrations from ``packages`` load only on a restart.
        if pkg.lovelace_changed or pkg.packages_changed or appended:
            self.state.pending_restart = True
        result.needs_restart = self.state.pending_restart

        changed = bool(
            outcome.applied or outcome.deleted or pkg.changed or appended or tk.changed_scopes
        )
        if result.errors or tk.aborted or result.check_result == "error":
            result.status = "partial"
        elif reconcile and not changed:
            result.status = "noop"
        else:
            result.status = "ok"

        if result.status in ("ok", "noop"):
            self.state.etag = new_etag
        self.state.release_id = release.release_id
        self.state.commit = release.commit
        self.state.published_at = release.published_at
        self.state.applied = planner.next_applied(
            self.state, plan_, scopes, outcome.deleted, failed_writes
        )
        self._save_state()

        await self._notify_bootstrap(bplan)
        await self._notify_conflict(bplan, pkg.path)
        await self._handle_restart(result, release)
        result.report_wanted = result.status != "noop" or self._report_is_stale()

    def _media_ready(self, result: SyncResult) -> bool:
        """``/media`` must be mounted by the Supervisor; ``wallpanel/`` inside it is ours.

        A missing media dir is not fatal: the scope is skipped for this run, the problem lands
        in ``errors`` (status ``partial``) and the other scopes proceed. Nothing is created in
        dry run.
        """
        media_dir = self.paths.media_dir
        if not media_dir.is_dir():
            message = MEDIA_DIR_MISSING.format(dir=media_dir)
            log.warning(message)
            result.errors.append(message)
            return False
        if self.options.dry_run:
            return True
        wallpanel = media_dir / WALLPANEL_DIR
        try:
            wallpanel.mkdir(exist_ok=True)
        except OSError as exc:
            message = MEDIA_DIR_UNWRITABLE.format(dir=wallpanel, exc=exc)
            log.error(message)
            result.errors.append(message)
            return False
        return True

    # ------------------------------------------------------------------------------------
    # Restart handling
    # ------------------------------------------------------------------------------------

    async def _handle_restart(self, result: SyncResult, release: Release) -> None:
        if not result.needs_restart:
            return
        if bootstrap.MISSING_PACKAGES in result.bootstrap_missing or (
            bootstrap.MISSING_PARSE in result.bootstrap_missing
        ):
            log.info(
                "Pakiet home_labs nie jest jeszcze dołączony w configuration.yaml — restart "
                "dopiero po uzupełnieniu konfiguracji"
            )
            return
        policy = self.options.restart_policy
        if policy == "never":
            log.info("Wymagany restart Home Assistant (polityka: never — tylko raport)")
            return
        if policy == "auto":
            reason = None
            if self.state.restart_done_for == release.release_id:
                reason = "restart dla tego wydania został już wykonany"
            elif result.check_result != "ok":
                reason = "sprawdzenie konfiguracji nie zwróciło OK"
            elif not in_window(self.options.restart_window, self.now().time()):
                reason = f"poza oknem restartu {self.options.restart_window}"
            if reason is None:
                if await self._auto_restart(result, release):
                    return
                reason = "restart nie powiódł się"
            log.info("Automatyczny restart pominięty: %s — wysyłam powiadomienie", reason)
        await self.hass.notify(
            NOTIFY_RESTART, RESTART_TITLE, RESTART_MESSAGE.format(commit=release.commit[:8])
        )

    async def _auto_restart(self, result: SyncResult, release: Release) -> bool:
        log.info("Automatyczny restart Home Assistant (wydanie %s)", release.commit[:8])
        self.state.restart_done_for = release.release_id
        self.state.pending_restart = False
        self._save_state()
        if not await self.hass.restart_core():
            self.state.pending_restart = True
            self._save_state()
            return False
        result.restart_done = True
        running = await self.hass.wait_until_running(self._restart_timeout, self._restart_poll)
        if running:
            log.info("Home Assistant uruchomił się ponownie")
        else:
            log.warning(
                "Home Assistant nie zgłosił stanu 'running' w %.0f s", self._restart_timeout
            )
        await self.hass.dismiss(NOTIFY_RESTART)
        return True

    async def _detect_manual_restart(self) -> None:
        """The REST-set sensor disappears when HA restarts: use it to clear ``pending_restart``."""
        if not self.state.pending_restart or not self.hass.available:
            return
        try:
            current = await self.hass.get_state(SENSOR_ENTITY_ID)
        except HassError as exc:
            log.debug("Nie udało się sprawdzić %s: %s", SENSOR_ENTITY_ID, exc)
            return
        if current is None:
            log.info("Home Assistant został uruchomiony ponownie — restart nie jest już wymagany")
            self.state.pending_restart = False
            await self.hass.dismiss(NOTIFY_RESTART)

    # ------------------------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------------------------

    async def _notify_bootstrap(self, bplan: bootstrap.BootstrapPlan) -> None:
        if bplan.missing:
            parts = [BOOTSTRAP_INTRO]
            for key in bplan.missing:
                instruction = bplan.instructions.get(key, "")
                intro, _, code = instruction.partition("\n")
                parts.append(f"**{key}**\n{intro}\n")
                if code:
                    parts.append(f"```yaml\n{code}\n```\n")
            await self.hass.notify(NOTIFY_BOOTSTRAP, BOOTSTRAP_TITLE, "\n".join(parts))
            self.state.notified_bootstrap = True
        elif self.state.notified_bootstrap:
            await self.hass.dismiss(NOTIFY_BOOTSTRAP)
            self.state.notified_bootstrap = False

    async def _notify_conflict(self, bplan: bootstrap.BootstrapPlan, package_path: str) -> None:
        if bplan.conflicts:
            keys = ", ".join(c.rsplit(".", 1)[-1] for c in bplan.conflicts)
            await self.hass.notify(
                NOTIFY_CONFLICT,
                CONFLICT_TITLE,
                CONFLICT_MESSAGE.format(keys=keys, package=package_path),
            )
            self.state.notified_conflict = True
        elif self.state.notified_conflict:
            await self.hass.dismiss(NOTIFY_CONFLICT)
            self.state.notified_conflict = False

    # ------------------------------------------------------------------------------------
    # Sensor, report, state
    # ------------------------------------------------------------------------------------

    async def _publish_sensor(self, result: SyncResult) -> None:
        if result.status == "unreachable":
            if self.state.consecutive_failures >= 3:
                state_str = "error"
            else:
                state_str = self.state.last_status or "noop"
        else:
            state_str = result.sensor_state
        if state_str not in SENSOR_STATES:
            state_str = "error"
        attributes: dict[str, Any] = {
            "friendly_name": "Home Labs Sync",
            "icon": "mdi:cloud-sync",
            "addon_version": VERSION,
            "release_id": result.release_id or self.state.release_id,
            "commit": result.commit or self.state.commit,
            "published_at": result.published_at or self.state.published_at,
            "last_sync": result.started_at.isoformat(timespec="seconds"),
            "last_trigger": result.trigger,
            "last_status": result.status,
            "needs_restart": bool(result.needs_restart or self.state.pending_restart),
            "check_result": result.check_result,
            "consecutive_failures": self.state.consecutive_failures,
            "errors": clip("; ".join(result.errors), 500),
            "bootstrap_missing": result.bootstrap_missing,
            "bootstrap_conflict": result.bootstrap_conflict,
        }
        await self.hass.set_state(SENSOR_ENTITY_ID, state_str, attributes)

    async def _send_report(self, result: SyncResult) -> None:
        ha_version = await self.hass.ha_version()
        report = build_report(result, VERSION, ha_version)
        try:
            await self.client.post_report(report)
        except AdminApiError as exc:
            log.warning("Nie udało się wysłać raportu: %s", exc)
            return
        result.report_sent = True
        self.state.last_report_at = self.now().isoformat(timespec="seconds")

    def _report_is_stale(self) -> bool:
        last = parse_iso(self.state.last_report_at)
        if last is None:
            return True
        return self.now() - last >= REPORT_STALE_AFTER

    def _save_state(self) -> None:
        try:
            self.state.save()
        except OSError as exc:
            log.error("Nie udało się zapisać stanu dodatku: %s", exc)

    # ------------------------------------------------------------------------------------
    # Entities export
    # ------------------------------------------------------------------------------------

    async def _handle_export_request(self, manifest: Manifest) -> None:
        requested = manifest.entities_export_requested
        if not requested:
            return
        if not _is_newer(requested, self.state.last_entities_request):
            return
        log.info("Home Labs poprosił o eksport encji (%s)", requested)
        self.state.last_entities_request = requested
        self._save_state()
        await self.export_entities("requested")

    def export_template(self) -> str:
        return self.state.export_template or entities.bundled_template()

    async def export_entities(self, trigger: str) -> ExportResult:
        self.exporting = True
        try:
            if not self.options.api_token:
                res = ExportResult(trigger, self.now(), False, message="Brak tokenu API")
            else:
                res = await entities.run_export(
                    self.hass, self.client, self.export_template(), trigger, self.now()
                )
            self.last_export = res
            if res.ok:
                self.state.last_entities_export_at = res.at.isoformat(timespec="seconds")
                self._save_state()
            return res
        finally:
            self.exporting = False

    def export_due(self) -> bool:
        return entities.schedule_due(
            self.options.entities_export_schedule,
            self.options.entities_export_hour,
            parse_iso(self.state.last_entities_export_at),
            self.now(),
        )


def _guess_release_id(data: Any) -> str | None:
    if isinstance(data, dict) and isinstance(data.get("release"), dict):
        rid = data["release"].get("release_id")
        if isinstance(rid, str) and rid:
            return rid[:200]
    return None


def _is_newer(candidate: str, previous: str | None) -> bool:
    if not previous:
        return True
    a, b = parse_iso(candidate), parse_iso(previous)
    if a is not None and b is not None:
        return a > b
    return candidate != previous
