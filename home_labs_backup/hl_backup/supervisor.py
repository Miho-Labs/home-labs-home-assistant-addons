"""Supervisor and HA core calls: discovery for the integration, notifications, token check."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from . import USER_AGENT

log = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)
SERVICE = "home_labs_backup"


class SupervisorApi:
    """Thin wrapper over ``http://supervisor``; without a token (standalone) it logs and skips."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._token = token

    @property
    def available(self) -> bool:
        return bool(self._token)

    async def _request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {self._token}"}
        async with self._session.request(
            method, self._base + path, json=body, headers=headers, timeout=TIMEOUT
        ) as resp:
            text = await resp.text()
            try:
                data = json.loads(text) if text else None
            except json.JSONDecodeError:
                data = text
            return resp.status, data

    async def send_discovery(self, config: dict[str, Any]) -> str | None:
        """Announce the integration's config; returns the discovery uuid."""
        if not self.available:
            log.info("Tryb samodzielny — pomijam discovery")
            return None
        status, data = await self._request(
            "POST", "/discovery", {"service": SERVICE, "config": config}
        )
        if status != 200:
            raise RuntimeError(f"Supervisor odrzucił discovery (HTTP {status}): {data}")
        return ((data or {}).get("data") or {}).get("uuid")

    async def notify(self, notification_id: str, title: str, message: str) -> None:
        if not self.available:
            log.info("Powiadomienie (pominięte): %s — %s", title, message)
            return
        body = {"notification_id": notification_id, "title": title, "message": message}
        status, data = await self._request(
            "POST", "/core/api/services/persistent_notification/create", body
        )
        if status >= 400:
            log.warning("Nie udało się utworzyć powiadomienia (HTTP %s): %s", status, data)

    async def dismiss(self, notification_id: str) -> None:
        if not self.available:
            return
        await self._request(
            "POST",
            "/core/api/services/persistent_notification/dismiss",
            {"notification_id": notification_id},
        )


async def check_token(session: aiohttp.ClientSession, api_url: str, token: str) -> tuple[str, str]:
    """``GET /backup/v1/status``. Returns (outcome, message); outcome is
    ``ok``, ``disabled``, ``auth``, ``unavailable`` or ``error``."""
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    try:
        async with session.get(
            f"{api_url.rstrip('/')}/backup/v1/status", headers=headers, timeout=TIMEOUT
        ) as resp:
            text = await resp.text()
            status = resp.status
    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
        return "error", f"brak połączenia z {api_url}: {exc.__class__.__name__}: {exc}"
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        data = {}
    if status == 401:
        return "auth", "token odrzucony — wygeneruj nowy w panelu Home Labs"
    if status == 503:
        return "unavailable", str(data.get("detail") or "usługa chwilowo niedostępna")
    if status != 200:
        return "error", f"HTTP {status}: {data.get('detail') if isinstance(data, dict) else text}"
    used = int(data.get("used_bytes", 0)) / 1024**3
    quota = int(data.get("quota_bytes", 0)) / 1024**3
    summary = (
        f"dom „{data.get('client')}”, kopii: {data.get('backups', 0)}, "
        f"zajęte {used:.1f} z {quota:.0f} GB"
    )
    if not data.get("enabled", True):
        return "disabled", f"kopie wyłączone przez Home Labs ({summary})"
    return "ok", summary
