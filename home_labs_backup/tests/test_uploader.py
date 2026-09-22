"""Multipart upload: equal parts, progress, retries, URL refresh, abort."""

from __future__ import annotations

import asyncio

import pytest

from hlb_integration import uploader
from hlb_integration.api import ApiConnectionError, ApiError


class FakeApi:
    def __init__(self, part_size: int = 4, max_parts: int = 10_000) -> None:
        self.part_size = part_size
        self.max_parts = max_parts
        self.url_requests: list[list[int]] = []
        self.parts: dict[int, bytes] = {}
        self.completed: list[tuple[int, str]] | None = None
        self.aborted = False
        self.meta = None
        self.fail_put: list[Exception] = []
        self.url_gen = 0

    async def start_upload(self, meta):
        self.meta = meta
        return {"upload_id": "u1", "part_size": self.part_size, "max_parts": self.max_parts}

    async def part_urls(self, upload_id, numbers):
        self.url_requests.append(numbers)
        self.url_gen += 1
        return {n: f"https://r2/{n}?gen={self.url_gen}" for n in numbers}

    async def put_part(self, url, data):
        if self.fail_put:
            raise self.fail_put.pop(0)
        number = int(url.split("/")[-1].split("?")[0])
        self.parts[number] = data
        return f'"etag-{number}"'

    async def complete(self, upload_id, parts):
        self.completed = parts
        return {"backup_id": "b", "size": sum(len(p) for p in self.parts.values())}

    async def abort(self, upload_id):
        self.aborted = True


async def _stream(*chunks: bytes):
    for c in chunks:
        yield c


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(uploader, "RETRY_DELAY", 0)


async def test_parts_are_equal_except_the_last_and_progress_is_reported():
    api = FakeApi(part_size=4)
    progress: list[int] = []
    result = await uploader.upload_backup(
        api,
        _stream(b"ab", b"cdefg", b"hijklmn", b"o"),
        {"backup_id": "b"},
        lambda *, bytes_uploaded: progress.append(bytes_uploaded),
    )
    assert [api.parts[n] for n in sorted(api.parts)] == [b"abcd", b"efgh", b"ijkl", b"mno"]
    assert api.completed == [(1, '"etag-1"'), (2, '"etag-2"'), (3, '"etag-3"'), (4, '"etag-4"')]
    assert progress == [4, 8, 12, 15]
    assert result["size"] == 15 and not api.aborted


async def test_urls_come_in_batches():
    api = FakeApi(part_size=1)
    await uploader.upload_backup(api, _stream(b"x" * 25), {}, None)
    assert api.url_requests == [list(range(1, 11)), list(range(11, 21)), list(range(21, 31))]


async def test_urls_never_ask_past_max_parts():
    api = FakeApi(part_size=1, max_parts=3)
    await uploader.upload_backup(api, _stream(b"abc"), {}, None)
    assert api.url_requests == [[1, 2, 3]]


async def test_too_many_parts_aborts():
    api = FakeApi(part_size=1, max_parts=2)
    with pytest.raises(ApiError):
        await uploader.upload_backup(api, _stream(b"abc"), {}, None)
    assert api.aborted and api.completed is None


async def test_old_urls_are_refreshed(monkeypatch):
    api = FakeApi(part_size=1)
    # fetch for part 1 at t=0; part 2 is looked at past the max age and re-fetched.
    clock = iter([0.0, uploader.URL_MAX_AGE + 1, uploader.URL_MAX_AGE + 1])
    monkeypatch.setattr(uploader, "_now", lambda: next(clock))
    await uploader.upload_backup(api, _stream(b"ab"), {}, None)
    assert api.url_requests == [list(range(1, 11)), list(range(2, 12))]


async def test_network_errors_are_retried():
    api = FakeApi(part_size=4)
    api.fail_put = [ApiConnectionError("reset"), ApiError("R2 500", 500)]
    await uploader.upload_backup(api, _stream(b"abcd"), {}, None)
    assert api.parts == {1: b"abcd"} and not api.aborted


async def test_expired_signature_gets_a_fresh_url():
    api = FakeApi(part_size=4)
    api.fail_put = [ApiError("expired", 403)]
    await uploader.upload_backup(api, _stream(b"abcd"), {}, None)
    assert api.url_requests == [list(range(1, 11)), list(range(1, 11))]


async def test_persistent_failure_aborts_the_upload():
    api = FakeApi(part_size=4)
    api.fail_put = [ApiConnectionError("down")] * uploader.PART_ATTEMPTS
    with pytest.raises(ApiConnectionError):
        await uploader.upload_backup(api, _stream(b"abcd"), {}, None)
    assert api.aborted and api.completed is None


async def test_a_client_error_is_not_retried():
    api = FakeApi(part_size=4)
    api.fail_put = [ApiError("bad request", 400), ApiError("never reached", 400)]
    with pytest.raises(ApiError, match="bad request"):
        await uploader.upload_backup(api, _stream(b"abcd"), {}, None)
    assert api.aborted


async def test_cancellation_still_aborts():
    api = FakeApi(part_size=4)

    async def slow():
        yield b"abcd"
        await asyncio.sleep(10)
        yield b"efgh"

    task = asyncio.create_task(uploader.upload_backup(api, slow(), {}, None))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert api.aborted


async def test_an_empty_stream_uploads_one_empty_part():
    api = FakeApi()
    await uploader.upload_backup(api, _stream(), {}, None)
    assert api.parts == {1: b""} and api.completed == [(1, '"etag-1"')]
