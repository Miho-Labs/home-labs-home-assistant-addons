"""Logging setup with a ring buffer for the ingress panel."""

from __future__ import annotations

import collections
import logging
import sys

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING}


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 200) -> None:
        super().__init__()
        self.buffer: collections.deque[str] = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(self.format(record))
        except Exception:
            self.handleError(record)

    def lines(self, count: int | None = None) -> list[str]:
        items = list(self.buffer)
        return items if count is None else items[-count:]


def setup_logging(level_name: str, capacity: int = 200) -> RingBufferHandler:
    level = LEVELS.get(level_name, logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)
    ring = RingBufferHandler(capacity)
    ring.setFormatter(fmt)
    root.addHandler(ring)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    return ring
