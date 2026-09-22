"""HTTP client for the Home Labs admin server (``/sync/v1/*``)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from . import USER_AGENT

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
FILE_TIMEOUT = 180.0


class AdminApiError(Exception):
    """Base class for admin server errors."""

    status: int | None = None

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NetworkError(AdminApiError):
    """Connection, DNS, TLS or timeout problem."""


class AuthError(AdminApiError):
    """401/403: bad, revoked or expired token."""


class RateLimitedError(AdminApiError):
    """429 Too Many Requests."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message, 429)
        self.retry_after = retry_after


class SyncDisabledError(AdminApiError):
    """503: sync disabled server-side."""


class ServerError(AdminApiError):
    """5xx or an unexpected/undecodable response."""


class RejectedError(AdminApiError):
    """4xx other than auth/rate limit (e.g. 422 on entities upload)."""


class BlobNotFoundError(AdminApiError):
    """404 on ``/sync/v1/files/{sha}``."""


class BlobInvalidError(AdminApiError):
    """Downloaded blob does not match its sha256 or exceeds the size limit."""


@dataclass
class ManifestResponse:
    status: int
    data: Any
    etag: str | None


class AdminClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return self._base

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}", "User-Agent": USER_AGENT}
        if extra:
            headers.update(extra)
        return headers

    async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
        body = await _safe_text(resp)
        detail = _detail(body) or resp.reason or ""
        status = resp.status
        if status in (401, 403):
            raise AuthError(f"HTTP {status}: {detail}", status)
        if status == 429:
            retry = resp.headers.get("Retry-After")
            try:
                retry_after = float(retry) if retry else None
            except ValueError:
                retry_after = None
            raise RateLimitedError(f"HTTP 429: {detail}", retry_after)
        if status == 503:
            raise SyncDisabledError("HTTP 503: synchronizacja wyłączona po stronie serwera", status)
        if status >= 500:
            raise ServerError(f"HTTP {status}: {detail}", status)
        raise RejectedError(f"HTTP {status}: {detail}", status)

    async def get_manifest(self, etag: str | None = None) -> ManifestResponse:
        extra = {"If-None-Match": etag} if etag else None
        url = f"{self._base}/sync/v1/manifest"
        try:
            async with self._session.get(
                url,
                headers=self._headers(extra),
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                if resp.status == 304:
                    return ManifestResponse(304, None, etag)
                if resp.status != 200:
                    await self._raise_for_status(resp)
                body = await resp.read()
                try:
                    data = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ServerError(f"manifest nie jest poprawnym JSON: {exc}", 200) from exc
                return ManifestResponse(200, data, resp.headers.get("ETag"))
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise NetworkError(_describe(exc)) from exc

    async def get_file(self, sha256: str, max_size: int) -> bytes:
        url = f"{self._base}/sync/v1/files/{sha256}"
        try:
            async with self._session.get(
                url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=FILE_TIMEOUT)
            ) as resp:
                if resp.status == 404:
                    raise BlobNotFoundError(f"plik {sha256[:12]}… nie należy do tego wydania", 404)
                if resp.status != 200:
                    await self._raise_for_status(resp)
                buf = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    buf += chunk
                    if len(buf) > max_size:
                        raise BlobInvalidError(f"plik {sha256[:12]}… przekracza limit {max_size} B")
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise NetworkError(_describe(exc)) from exc
        digest = hashlib.sha256(buf).hexdigest()
        if digest != sha256.lower():
            raise BlobInvalidError(f"suma SHA-256 pliku {sha256[:12]}… nie zgadza się")
        return bytes(buf)

    async def post_report(self, report: dict[str, Any]) -> None:
        url = f"{self._base}/sync/v1/report"
        try:
            async with self._session.post(
                url,
                headers=self._headers(),
                json=report,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                if resp.status not in (200, 202, 204):
                    await self._raise_for_status(resp)
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise NetworkError(_describe(exc)) from exc

    async def post_entities(self, csv_text: str, trigger: str) -> dict[str, Any]:
        url = f"{self._base}/sync/v1/entities"
        headers = self._headers(
            {"Content-Type": "text/csv; charset=utf-8", "X-HL-Trigger": trigger}
        )
        try:
            async with self._session.post(
                url,
                headers=headers,
                data=csv_text.encode("utf-8"),
                timeout=aiohttp.ClientTimeout(total=self._timeout * 2),
            ) as resp:
                if resp.status not in (200, 202):
                    await self._raise_for_status(resp)
                body = await _safe_text(resp)
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise NetworkError(_describe(exc)) from exc
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}
        return data if isinstance(data, dict) else {}


async def _safe_text(resp: aiohttp.ClientResponse, limit: int = 4000) -> str:
    try:
        raw = await resp.content.read(limit)
    except (TimeoutError, aiohttp.ClientError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _detail(body: str) -> str:
    if not body:
        return ""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body.strip()[:300]
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("message") or data.get("error")
        if detail:
            return str(detail)[:300]
    return body.strip()[:300]


def _describe(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "przekroczono czas oczekiwania na odpowiedź serwera"
    text = str(exc) or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {text}"
