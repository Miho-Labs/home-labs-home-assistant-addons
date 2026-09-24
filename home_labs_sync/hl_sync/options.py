"""Add-on options (``/data/options.json``) and runtime paths."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

log = logging.getLogger(__name__)

SCOPES: tuple[str, ...] = ("dashboards", "themes", "automations", "scenes", "media", "packages")
RESTART_POLICIES = ("never", "notify", "auto")
EXPORT_SCHEDULES = ("off", "daily", "weekly")
LOG_LEVELS = ("debug", "info", "warning")

DEFAULT_API_URL = "https://public.home-labs.pl"
DEFAULT_CONFIG_CANDIDATES = ("/homeassistant", "/config")
DEFAULT_MEDIA_DIR = "/media"
DEFAULT_DATA_DIR = "/data"
DEFAULT_SUPERVISOR_URL = "http://supervisor"


@dataclass
class Options:
    """Typed view of the add-on options; mirrors ``config.yaml`` defaults."""

    api_url: str = DEFAULT_API_URL
    api_token: str = ""
    interval_minutes: int = 30
    scopes: tuple[str, ...] = SCOPES
    restart_policy: str = "auto"
    restart_window: str = "02:00-05:00"
    entities_export_schedule: str = "off"
    entities_export_hour: int = 4
    dry_run: bool = False
    log_level: str = "info"

    def __post_init__(self) -> None:
        self.api_url = str(self.api_url or DEFAULT_API_URL).strip().rstrip("/")
        self.api_token = str(self.api_token or "").strip()
        self.interval_minutes = _clamp(_to_int(self.interval_minutes, 30), 5, 1440)
        self.scopes = _normalize_scopes(self.scopes)
        if self.restart_policy not in RESTART_POLICIES:
            log.warning("Nieznana polityka restartu %r, używam 'auto'", self.restart_policy)
            self.restart_policy = "auto"
        try:
            parse_window(self.restart_window)
        except ValueError:
            log.warning("Nieprawidłowe okno restartu %r, używam 02:00-05:00", self.restart_window)
            self.restart_window = "02:00-05:00"
        if self.entities_export_schedule not in EXPORT_SCHEDULES:
            self.entities_export_schedule = "off"
        self.entities_export_hour = _clamp(_to_int(self.entities_export_hour, 4), 0, 23)
        self.dry_run = bool(self.dry_run)
        if self.log_level not in LOG_LEVELS:
            self.log_level = "info"


def _to_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _normalize_scopes(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return SCOPES
    seen: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name in SCOPES and name not in seen:
            seen.append(name)
        elif name not in SCOPES:
            log.warning("Nieznany zakres synchronizacji %r — pomijam", name)
    return tuple(seen)


def load_options(path: Path) -> Options:
    """Read ``options.json``; missing or broken file yields defaults (with a warning)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("Brak pliku opcji %s — używam wartości domyślnych", path)
        raw = {}
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Nie udało się odczytać opcji %s: %s — używam wartości domyślnych", path, exc)
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    known = {f for f in Options.__dataclass_fields__}
    return Options(**{k: v for k, v in raw.items() if k in known})


def parse_window(window: str) -> tuple[time, time]:
    """Parse ``HH:MM-HH:MM`` into two ``time`` objects. Raises ``ValueError``."""
    try:
        start_s, end_s = str(window).split("-", 1)
        sh, sm = (int(x) for x in start_s.strip().split(":", 1))
        eh, em = (int(x) for x in end_s.strip().split(":", 1))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid window {window!r}") from exc
    if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
        raise ValueError(f"invalid window {window!r}")
    return time(sh, sm), time(eh, em)


def in_window(window: str, now: time) -> bool:
    """True when ``now`` (local time) lies in the window; the window may wrap midnight."""
    start, end = parse_window(window)
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


@dataclass
class Paths:
    """Filesystem locations used by the add-on."""

    config_dir: Path
    data_dir: Path
    options_file: Path = field(default=None)  # type: ignore[assignment]
    media_dir: Path = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.config_dir = Path(self.config_dir)
        self.data_dir = Path(self.data_dir)
        if self.options_file is None:
            self.options_file = self.data_dir / "options.json"
        self.options_file = Path(self.options_file)
        if self.media_dir is None:
            self.media_dir = Path(DEFAULT_MEDIA_DIR)
        self.media_dir = Path(self.media_dir)

    @property
    def roots(self) -> dict[str, Path]:
        """Base directory for each manifest ``root`` (see ``manifest.ROOTS``)."""
        return {"config": self.config_dir, "media": self.media_dir}

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def stage_root(self) -> Path:
        return self.data_dir / "stage"

    @property
    def backup_root(self) -> Path:
        return self.data_dir / "backup"


def resolve_paths(env: dict[str, str] | None = None) -> Paths:
    """Resolve config/data/media dirs from env overrides, else probe /homeassistant then /config.

    The media dir (``/media``, ``HL_SYNC_MEDIA_DIR``) is not probed here: whether it exists is
    checked at sync time, so a missing mount only disables the ``media`` scope.
    """
    env = os.environ if env is None else env
    config_dir = env.get("HL_SYNC_CONFIG_DIR")
    if not config_dir:
        for candidate in DEFAULT_CONFIG_CANDIDATES:
            if (Path(candidate) / "configuration.yaml").is_file():
                config_dir = candidate
                break
        else:
            config_dir = DEFAULT_CONFIG_CANDIDATES[0]
            log.warning(
                "Nie znaleziono configuration.yaml w %s — używam %s",
                " ani ".join(DEFAULT_CONFIG_CANDIDATES),
                config_dir,
            )
    data_dir = env.get("HL_SYNC_DATA_DIR") or DEFAULT_DATA_DIR
    options_file = env.get("HL_SYNC_OPTIONS_JSON") or str(Path(data_dir) / "options.json")
    media_dir = env.get("HL_SYNC_MEDIA_DIR") or DEFAULT_MEDIA_DIR
    return Paths(Path(config_dir), Path(data_dir), Path(options_file), Path(media_dir))


def supervisor_url(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return (env.get("HL_SYNC_SUPERVISOR_URL") or DEFAULT_SUPERVISOR_URL).rstrip("/")


def supervisor_token(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("SUPERVISOR_TOKEN", "")
