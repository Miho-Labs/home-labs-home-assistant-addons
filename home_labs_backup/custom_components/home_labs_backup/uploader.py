"""Multipart upload of one backup stream — no Home Assistant imports.

The buffering follows HA's own cloudflare_r2 agent: every part but the last
is exactly `part_size` (R2 requires equal parts), and only one part is held
in memory at a time. Differences: the part size comes from the server, and
each part goes to a presigned URL the admin issued for this house's prefix.

URLs are fetched in small batches right before use and re-fetched when they
may have expired (a slow uplink can take minutes per part), or when R2
answers 403 for one. A part that fails for a network reason is retried; if
the upload cannot finish, the server is told to abort it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from .api import ApiConnectionError, ApiError, HomeLabsBackupApi

_LOGGER = logging.getLogger(__name__)

URL_BATCH = 10
# Presigned URLs live an hour server-side; refresh well before that.
URL_MAX_AGE = 40 * 60
PART_ATTEMPTS = 4
RETRY_DELAY = 2.0


def _now() -> float:
    return time.monotonic()


async def _parts(stream: AsyncIterator[bytes], part_size: int) -> AsyncIterator[bytes]:
    buffer = bytearray()
    async for chunk in stream:
        buffer.extend(chunk)
        while len(buffer) >= part_size:
            part = bytes(buffer[:part_size])
            del buffer[:part_size]
            yield part
    if buffer:
        yield bytes(buffer)


class _Urls:
    """Presigned part URLs, fetched in batches and dropped when they get old."""

    def __init__(self, api: HomeLabsBackupApi, upload_id: str, max_parts: int) -> None:
        self._api = api
        self._upload_id = upload_id
        self._max_parts = max_parts
        self._urls: dict[int, tuple[str, float]] = {}

    async def get(self, number: int, *, fresh: bool = False) -> str:
        cached = self._urls.get(number)
        if fresh or cached is None or _now() - cached[1] > URL_MAX_AGE:
            last = min(number + URL_BATCH - 1, self._max_parts)
            got = await self._api.part_urls(self._upload_id, list(range(number, last + 1)))
            now = _now()
            self._urls = {n: (url, now) for n, url in got.items()}
            if number not in self._urls:
                raise ApiError(f"Serwer nie wydał adresu dla części {number}")
        return self._urls[number][0]


async def _put(urls: _Urls, api: HomeLabsBackupApi, number: int, data: bytes) -> str:
    fresh = False
    for attempt in range(1, PART_ATTEMPTS + 1):
        url = await urls.get(number, fresh=fresh)
        try:
            return await api.put_part(url, data)
        except ApiConnectionError as err:
            if attempt == PART_ATTEMPTS:
                raise
            _LOGGER.warning("Część %d: %s — ponawiam (%d/%d)", number, err, attempt, PART_ATTEMPTS)
        except ApiError as err:
            # 403 = the signature expired (or the clock drifted); 5xx = R2 hiccup.
            if attempt == PART_ATTEMPTS or not (err.status == 403 or (err.status or 0) >= 500):
                raise
            fresh = err.status == 403
            _LOGGER.warning("Część %d: %s — ponawiam (%d/%d)", number, err, attempt, PART_ATTEMPTS)
        await asyncio.sleep(RETRY_DELAY * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


async def upload_backup(
    api: HomeLabsBackupApi,
    stream: AsyncIterator[bytes],
    meta: dict[str, Any],
    on_progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Upload one backup (`meta` is HA's AgentBackup.as_dict()); returns the server's summary."""
    started = await api.start_upload(meta)
    upload_id = str(started["upload_id"])
    part_size = int(started["part_size"])
    max_parts = int(started.get("max_parts", 10_000))
    urls = _Urls(api, upload_id, max_parts)
    try:
        done: list[tuple[int, str]] = []
        uploaded = 0
        number = 0
        async for part in _parts(stream, part_size):
            number += 1
            if number > max_parts:
                raise ApiError(f"Kopia przekracza {max_parts} części po {part_size} B")
            done.append((number, await _put(urls, api, number, part)))
            uploaded += len(part)
            if on_progress is not None:
                on_progress(bytes_uploaded=uploaded)
        if not done:
            # S3 multipart needs at least one part, even an empty one.
            done.append((1, await _put(urls, api, 1, b"")))
        return await api.complete(upload_id, done)
    except BaseException:
        # Shielded: HA cancelling the upload must still release the parts.
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.shield(api.abort(upload_id))
        raise
