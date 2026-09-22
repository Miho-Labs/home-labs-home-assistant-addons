"""Client of the Home Labs admin's /backup/v1 API — no Home Assistant imports.

Two kinds of request leave this module and they must never be mixed up:
calls to the admin carry the `hls_` bearer token; PUTs of backup parts and
GETs of downloads go to presigned R2 URLs and carry **no** Authorization
header — the token is ours, not Cloudflare's.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

API_TIMEOUT = aiohttp.ClientTimeout(total=30)
# A 32 MiB part on a slow uplink: ~5 min at 1 Mbit/s, so leave room.
PART_TIMEOUT = aiohttp.ClientTimeout(total=30 * 60, sock_connect=30)
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300)
DOWNLOAD_CHUNK = 1024 * 1024


class ApiError(Exception):
    """Anything the admin or R2 refused; `str()` is a message for the owner."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ApiConnectionError(ApiError):
    """DNS, TLS, timeout — the server was not reached."""


class ApiAuthError(ApiError):
    """401: token wrong, revoked or expired."""


class ApiDisabledError(ApiError):
    """403: cloud backups switched off for this house in the Home Labs panel."""


class ApiNotFoundError(ApiError):
    """404: no such backup (or upload) for this token."""


class ApiUnavailableError(ApiError):
    """503: backups not configured server-side, or a maintenance break."""


def _detail(body: str) -> str:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body.strip()[:300]
    if isinstance(data, dict) and data.get("detail"):
        detail = data["detail"]
        return detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)[:300]
    return body.strip()[:300]


def _describe(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "przekroczono czas oczekiwania na serwer"
    return f"{exc.__class__.__name__}: {exc}" if str(exc) else exc.__class__.__name__


class HomeLabsBackupApi:
    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, token: str, user_agent: str
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/") + "/backup/v1"
        self._token = token
        self._user_agent = user_agent

    # ------------------------------------------------------------ admin API

    async def _request(self, method: str, path: str, *, json_body: Any = None) -> Any:
        headers = {"Authorization": f"Bearer {self._token}", "User-Agent": self._user_agent}
        try:
            async with self._session.request(
                method, self._base + path, json=json_body, headers=headers, timeout=API_TIMEOUT
            ) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    raise self._error(resp.status, body)
        except ApiError:
            raise
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise ApiConnectionError(f"Brak połączenia z Home Labs: {_describe(exc)}") from exc
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ApiError(f"Niepoprawna odpowiedź serwera Home Labs: {exc}", resp.status) from exc

    @staticmethod
    def _error(status: int, body: str) -> ApiError:
        detail = _detail(body) or f"HTTP {status}"
        if status == 401:
            return ApiAuthError(detail, status)
        if status == 403:
            return ApiDisabledError(detail, status)
        if status == 404:
            return ApiNotFoundError(detail, status)
        if status == 503:
            return ApiUnavailableError(detail, status)
        return ApiError(detail, status)

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/status")

    async def list_backups(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/backups")
        backups = (data or {}).get("backups", [])
        return [b for b in backups if isinstance(b, dict)]

    async def start_upload(self, meta: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/uploads", json_body=meta)

    async def part_urls(self, upload_id: str, numbers: list[int]) -> dict[int, str]:
        data = await self._request(
            "POST", f"/uploads/{upload_id}/parts", json_body={"part_numbers": numbers}
        )
        return {int(n): url for n, url in (data or {}).get("urls", {}).items()}

    async def complete(self, upload_id: str, parts: list[tuple[int, str]]) -> dict[str, Any]:
        body = {"parts": [{"part_number": n, "etag": etag} for n, etag in parts]}
        return await self._request("POST", f"/uploads/{upload_id}/complete", json_body=body)

    async def abort(self, upload_id: str) -> None:
        await self._request("DELETE", f"/uploads/{upload_id}")

    async def download_url(self, backup_id: str) -> str:
        data = await self._request("GET", f"/backups/{backup_id}/download")
        return data["url"]

    async def delete(self, backup_id: str) -> None:
        await self._request("DELETE", f"/backups/{backup_id}")

    # ------------------------------------------------------------ R2 (presigned)

    async def put_part(self, url: str, data: bytes) -> str:
        """PUT one part to its presigned URL; returns the ETag R2 assigned."""
        try:
            async with self._session.put(
                url, data=data, headers={"Content-Length": str(len(data))}, timeout=PART_TIMEOUT
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise ApiError(
                        f"Magazyn kopii odrzucił część (HTTP {resp.status}): {body[:200]}",
                        resp.status,
                    )
                etag = resp.headers.get("ETag")
        except ApiError:
            raise
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise ApiConnectionError(f"Wysyłanie części przerwane: {_describe(exc)}") from exc
        if not etag:
            raise ApiError("Magazyn kopii nie zwrócił ETag dla części")
        return etag

    async def iter_download(self, url: str) -> AsyncIterator[bytes]:
        """Stream a presigned GET. Opened eagerly, so a 4xx surfaces before HA starts reading."""
        try:
            resp = await self._session.get(url, timeout=DOWNLOAD_TIMEOUT)
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise ApiConnectionError(f"Pobieranie kopii przerwane: {_describe(exc)}") from exc
        if resp.status >= 400:
            body = await resp.text()
            resp.release()
            raise ApiError(f"Magazyn kopii odmówił pobrania (HTTP {resp.status}): {body[:200]}")

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK):
                    yield chunk
            except (TimeoutError, aiohttp.ClientError, asyncio.IncompleteReadError) as exc:
                raise ApiConnectionError(f"Pobieranie kopii przerwane: {_describe(exc)}") from exc
            finally:
                resp.release()

        return chunks()
