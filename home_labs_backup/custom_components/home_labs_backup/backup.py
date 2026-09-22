"""Backup platform: "Home Labs (chmura)" as a backup location.

Modelled on HA's cloudflare_r2 agent, but without R2 credentials: metadata
goes to the Home Labs admin (which also lists it back), the bytes go to R2
through presigned URLs the admin issues for this house only (uploader.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from time import time
from typing import Any

from homeassistant.components.backup import (
    AgentBackup,
    BackupAgent,
    BackupAgentError,
    BackupNotFound,
)
from homeassistant.core import HomeAssistant, callback

from . import HomeLabsBackupConfigEntry
from .api import ApiDisabledError, ApiError, ApiNotFoundError
from .const import CACHE_TTL, DATA_BACKUP_AGENT_LISTENERS, DOMAIN, TITLE
from .uploader import upload_backup

UNPROTECTED = (
    "Home Labs przyjmuje tylko szyfrowane kopie. Włącz szyfrowanie dla lokalizacji "
    f"„{TITLE}” w Ustawienia → System → Kopie zapasowe."
)


async def async_get_backup_agents(hass: HomeAssistant, **kwargs: Any) -> list[BackupAgent]:
    entries: list[HomeLabsBackupConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    return [HomeLabsBackupAgent(hass, entry) for entry in entries]


@callback
def async_register_backup_agents_listener(
    hass: HomeAssistant, *, listener: Callable[[], None], **kwargs: Any
) -> Callable[[], None]:
    hass.data.setdefault(DATA_BACKUP_AGENT_LISTENERS, []).append(listener)

    @callback
    def remove_listener() -> None:
        hass.data[DATA_BACKUP_AGENT_LISTENERS].remove(listener)
        if not hass.data[DATA_BACKUP_AGENT_LISTENERS]:
            del hass.data[DATA_BACKUP_AGENT_LISTENERS]

    return remove_listener


class HomeLabsBackupAgent(BackupAgent):
    domain = DOMAIN

    def __init__(self, hass: HomeAssistant, entry: HomeLabsBackupConfigEntry) -> None:
        super().__init__()
        self._api = entry.runtime_data
        self.name = TITLE
        self.unique_id = entry.entry_id
        self._cache: dict[str, AgentBackup] = {}
        self._cache_expires = 0.0

    async def async_upload_backup(
        self,
        *,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
        backup: AgentBackup,
        on_progress: Callable[..., None] | None = None,
        **kwargs: Any,
    ) -> None:
        # Refused here too, not only server-side: no point streaming gigabytes
        # to be told no, and the owner sees why in the backup screen.
        if not backup.protected:
            raise BackupAgentError(UNPROTECTED)
        try:
            await upload_backup(self._api, await open_stream(), backup.as_dict(), on_progress)
        except ApiDisabledError as err:
            raise BackupAgentError(f"Kopie w chmurze Home Labs są wyłączone: {err}") from err
        except ApiError as err:
            raise BackupAgentError(f"Wysyłanie kopii do Home Labs nie powiodło się: {err}") from err
        finally:
            self._cache_expires = 0.0

    async def async_download_backup(self, backup_id: str, **kwargs: Any) -> AsyncIterator[bytes]:
        await self._find(backup_id)
        try:
            url = await self._api.download_url(backup_id)
            return await self._api.iter_download(url)
        except ApiNotFoundError as err:
            raise BackupNotFound(f"Kopia {backup_id} nie istnieje") from err
        except ApiError as err:
            raise BackupAgentError(f"Pobieranie kopii z Home Labs nie powiodło się: {err}") from err

    async def async_delete_backup(self, backup_id: str, **kwargs: Any) -> None:
        await self._find(backup_id)
        try:
            await self._api.delete(backup_id)
        except ApiNotFoundError as err:
            raise BackupNotFound(f"Kopia {backup_id} nie istnieje") from err
        except ApiError as err:
            raise BackupAgentError(f"Usuwanie kopii w Home Labs nie powiodło się: {err}") from err
        finally:
            self._cache_expires = 0.0

    async def async_list_backups(self, **kwargs: Any) -> list[AgentBackup]:
        return list((await self._list()).values())

    async def async_get_backup(self, backup_id: str, **kwargs: Any) -> AgentBackup:
        return await self._find(backup_id)

    async def _find(self, backup_id: str) -> AgentBackup:
        backups = await self._list()
        if backup := backups.get(backup_id):
            return backup
        raise BackupNotFound(f"Kopia {backup_id} nie istnieje")

    async def _list(self) -> dict[str, AgentBackup]:
        if time() < self._cache_expires:
            return self._cache
        try:
            raw = await self._api.list_backups()
        except ApiError as err:
            raise BackupAgentError(f"Nie udało się pobrać listy kopii z Home Labs: {err}") from err
        backups: dict[str, AgentBackup] = {}
        for item in raw:
            try:
                backup = AgentBackup.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            backups[backup.backup_id] = backup
        self._cache = backups
        self._cache_expires = time() + CACHE_TTL
        return backups
