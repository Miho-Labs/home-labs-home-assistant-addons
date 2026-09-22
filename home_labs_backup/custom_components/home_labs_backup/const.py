"""Constants for the Home Labs Backup integration."""

from __future__ import annotations

DOMAIN = "home_labs_backup"
# Must equal "version" in manifest.json (a test checks); the add-on compares
# the manifest to decide whether to reinstall.
VERSION = "0.1.1"
USER_AGENT = f"home-labs-backup/{VERSION}"
TITLE = "Home Labs (chmura)"

CONF_API_URL = "api_url"
CONF_API_TOKEN = "api_token"
DEFAULT_API_URL = "https://public.home-labs.pl"

DATA_BACKUP_AGENT_LISTENERS = f"{DOMAIN}.backup_agent_listeners"

# How long the listed backups are cached (the server is the source of truth).
CACHE_TTL = 60
