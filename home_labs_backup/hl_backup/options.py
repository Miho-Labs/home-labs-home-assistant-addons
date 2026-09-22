"""Add-on options (``/data/options.json``) and runtime paths."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

LOG_LEVELS = ("debug", "info", "warning")
DEFAULT_API_URL = "https://public.home-labs.pl"
DEFAULT_CONFIG_CANDIDATES = ("/homeassistant", "/config")
DEFAULT_DATA_DIR = "/data"
DEFAULT_SUPERVISOR_URL = "http://supervisor"
DEFAULT_INTEGRATION_SRC = "/usr/src/integration/home_labs_backup"


@dataclass
class Options:
    """Typed view of the add-on options; mirrors ``config.yaml`` defaults."""

    api_url: str = DEFAULT_API_URL
    api_token: str = ""
    log_level: str = "info"

    def __post_init__(self) -> None:
        self.api_url = str(self.api_url or DEFAULT_API_URL).strip().rstrip("/")
        self.api_token = str(self.api_token or "").strip()
        if self.log_level not in LOG_LEVELS:
            self.log_level = "info"


def load_options(path: Path) -> Options:
    """Read ``options.json``; a missing or broken file yields defaults (with a warning)."""
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
    known = set(Options.__dataclass_fields__)
    return Options(**{k: v for k, v in raw.items() if k in known})


@dataclass
class Paths:
    config_dir: Path
    data_dir: Path
    integration_src: Path

    @property
    def options_file(self) -> Path:
        return self.data_dir / "options.json"

    @property
    def integration_dst(self) -> Path:
        return self.config_dir / "custom_components" / "home_labs_backup"


def resolve_paths(env: dict[str, str] | None = None) -> Paths:
    """Config dir from ``HL_BACKUP_CONFIG_DIR``, else the first of /homeassistant, /config."""
    env = os.environ if env is None else env
    config_dir = env.get("HL_BACKUP_CONFIG_DIR")
    if not config_dir:
        for candidate in DEFAULT_CONFIG_CANDIDATES:
            if (Path(candidate) / "configuration.yaml").is_file():
                config_dir = candidate
                break
        else:
            config_dir = DEFAULT_CONFIG_CANDIDATES[0]
            log.warning("Nie znaleziono configuration.yaml — używam %s", config_dir)
    return Paths(
        config_dir=Path(config_dir),
        data_dir=Path(env.get("HL_BACKUP_DATA_DIR") or DEFAULT_DATA_DIR),
        integration_src=Path(env.get("HL_BACKUP_INTEGRATION_SRC") or DEFAULT_INTEGRATION_SRC),
    )


def supervisor_url(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return (env.get("HL_BACKUP_SUPERVISOR_URL") or DEFAULT_SUPERVISOR_URL).rstrip("/")


def supervisor_token(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("SUPERVISOR_TOKEN", "")
