"""Home Labs Backup — Home Assistant backups in the Home Labs cloud.

Installed and configured by the Home Labs Backup add-on: the add-on copies
this integration into custom_components and hands it the server address and
the house's `hls_` token through Supervisor discovery (config_flow.py). The
backup platform (backup.py) then appears as a backup location in
Settings → System → Backups, like the built-in cloud storages.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiAuthError, ApiError, HomeLabsBackupApi
from .const import CONF_API_TOKEN, CONF_API_URL, DATA_BACKUP_AGENT_LISTENERS, DOMAIN, USER_AGENT

type HomeLabsBackupConfigEntry = ConfigEntry[HomeLabsBackupApi]


def create_api(hass: HomeAssistant, api_url: str, token: str) -> HomeLabsBackupApi:
    return HomeLabsBackupApi(async_get_clientsession(hass), api_url, token, USER_AGENT)


async def async_setup_entry(hass: HomeAssistant, entry: HomeLabsBackupConfigEntry) -> bool:
    """Check the token once, then expose the backup agent."""
    api = create_api(hass, entry.data[CONF_API_URL], entry.data[CONF_API_TOKEN])
    try:
        await api.status()
    except ApiAuthError as err:
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="invalid_token") from err
    except ApiError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    entry.runtime_data = api

    def notify_backup_listeners() -> None:
        for listener in hass.data.get(DATA_BACKUP_AGENT_LISTENERS, []):
            listener()

    entry.async_on_unload(entry.async_on_state_change(notify_backup_listeners))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeLabsBackupConfigEntry) -> bool:
    """Nothing to close: the aiohttp session is Home Assistant's."""
    return True
