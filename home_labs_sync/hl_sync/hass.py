"""Home Assistant Core + Supervisor API through the Supervisor proxy."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

RELOAD_SERVICES = {
    "automations": ("automation", "reload"),
    "scenes": ("scene", "reload"),
    "themes": ("frontend", "reload_themes"),
}
SENSOR_ENTITY_ID = "sensor.home_labs_sync"


class HassError(Exception):
    """A call to Home Assistant failed."""


class HassApi:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        supervisor_url: str,
        token: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._session = session
        self._base = supervisor_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._warned = False

    @property
    def available(self) -> bool:
        return bool(self._token)

    def _skip(self, what: str) -> None:
        if not self._warned:
            log.warning(
                "Brak SUPERVISOR_TOKEN — tryb samodzielny, pomijam wywołania Home Assistant"
            )
            self._warned = True
        log.debug("Pominięto (brak Supervisora): %s", what)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        timeout: float | None = None,
        expect_json: bool = True,
    ) -> tuple[int, Any]:
        url = f"{self._base}{path}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=timeout or self._timeout),
            ) as resp:
                raw = await resp.read()
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise HassError(f"{method} {path}: {exc.__class__.__name__}: {exc}") from exc
        text = raw.decode("utf-8", errors="replace")
        if not expect_json:
            return resp.status, text
        try:
            return resp.status, json.loads(text) if text else None
        except json.JSONDecodeError:
            return resp.status, text

    # -- Supervisor -------------------------------------------------------------------------

    async def core_info(self) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            status, body = await self._request("GET", "/core/info")
        except HassError as exc:
            log.warning("Nie udało się pobrać informacji o Home Assistant: %s", exc)
            return None
        if status != 200 or not isinstance(body, dict):
            return None
        data = body.get("data")
        return data if isinstance(data, dict) else None

    async def ha_version(self) -> str:
        info = await self.core_info()
        if info and info.get("version"):
            return str(info["version"])
        return "unknown"

    async def check_config(self) -> tuple[str, str]:
        """``POST /core/check`` -> ("ok"|"error"|"skipped", message)."""
        if not self.available:
            self._skip("sprawdzenie konfiguracji")
            return "skipped", ""
        try:
            status, body = await self._request("POST", "/core/check", timeout=300)
        except HassError as exc:
            log.warning("Sprawdzenie konfiguracji nie powiodło się: %s", exc)
            return "error", str(exc)
        message = ""
        if isinstance(body, dict):
            message = str(body.get("message") or "")
            if status == 200 and body.get("result", "ok") == "ok":
                return "ok", message
        elif status == 200:
            return "ok", ""
        else:
            message = str(body or "")
        return "error", message or f"HTTP {status}"

    async def restart_core(self) -> bool:
        if not self.available:
            self._skip("restart Home Assistant")
            return False
        try:
            status, body = await self._request("POST", "/core/restart", json_body={}, timeout=300)
        except HassError as exc:
            log.error("Restart Home Assistant nie powiódł się: %s", exc)
            return False
        if status != 200:
            log.error("Restart Home Assistant odrzucony: HTTP %s %s", status, body)
            return False
        return True

    async def wait_until_running(self, timeout: float = 300.0, poll: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            info = await self.core_info()
            if info and info.get("state") == "running":
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(poll)

    # -- Core REST API ----------------------------------------------------------------------

    async def call_service(
        self, domain: str, service: str, data: dict[str, Any] | None = None
    ) -> bool:
        if not self.available:
            self._skip(f"usługa {domain}.{service}")
            return False
        try:
            status, body = await self._request(
                "POST", f"/core/api/services/{domain}/{service}", json_body=data or {}, timeout=60
            )
        except HassError as exc:
            log.warning("Usługa %s.%s nie powiodła się: %s", domain, service, exc)
            return False
        if status not in (200, 201):
            log.warning("Usługa %s.%s zwróciła HTTP %s: %s", domain, service, status, body)
            return False
        return True

    async def reload_scopes(self, scopes: Iterable[str]) -> list[str]:
        """Reload automations/scenes/themes for the given scopes; returns error messages."""
        errors: list[str] = []
        for scope in sorted(set(scopes)):
            svc = RELOAD_SERVICES.get(scope)
            if not svc:
                continue
            if not self.available:
                self._skip(f"przeładowanie {scope}")
                continue
            ok = await self.call_service(*svc)
            if ok:
                log.info("Przeładowano %s.%s", *svc)
            else:
                errors.append(f"przeładowanie {svc[0]}.{svc[1]} nie powiodło się")
        return errors

    async def set_state(self, entity_id: str, state: str, attributes: dict[str, Any]) -> bool:
        if not self.available:
            self._skip(f"stan {entity_id}")
            return False
        try:
            status, body = await self._request(
                "POST",
                f"/core/api/states/{entity_id}",
                json_body={"state": state, "attributes": attributes},
            )
        except HassError as exc:
            log.warning("Nie udało się ustawić %s: %s", entity_id, exc)
            return False
        if status not in (200, 201):
            log.warning("Ustawienie %s zwróciło HTTP %s: %s", entity_id, status, body)
            return False
        return True

    async def get_state(self, entity_id: str) -> dict[str, Any] | None:
        """Entity state dict, ``None`` when the entity does not exist. Raises on API failure."""
        if not self.available:
            raise HassError("brak Supervisora")
        status, body = await self._request("GET", f"/core/api/states/{entity_id}")
        if status == 404:
            return None
        if status != 200 or not isinstance(body, dict):
            raise HassError(f"GET states/{entity_id}: HTTP {status}")
        return body

    async def notify(self, notification_id: str, title: str, message: str) -> bool:
        return await self.call_service(
            "persistent_notification",
            "create",
            {"notification_id": notification_id, "title": title, "message": message},
        )

    async def dismiss(self, notification_id: str) -> bool:
        return await self.call_service(
            "persistent_notification", "dismiss", {"notification_id": notification_id}
        )

    async def render_template(self, template: str) -> str:
        if not self.available:
            raise HassError("brak Supervisora — nie można wyrenderować szablonu")
        status, body = await self._request(
            "POST",
            "/core/api/template",
            json_body={"template": template},
            timeout=120,
            expect_json=False,
        )
        if status != 200:
            detail = body
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    detail = parsed.get("message") or body
            except (json.JSONDecodeError, TypeError):
                pass
            raise HassError(f"renderowanie szablonu: HTTP {status}: {str(detail)[:500]}")
        return body if isinstance(body, str) else str(body)
