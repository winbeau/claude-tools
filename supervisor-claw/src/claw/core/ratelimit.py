"""Per-domain token bucket. Simple, async-friendly."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class _Bucket:
    rate: float          # tokens per second
    capacity: float      # max tokens
    tokens: float
    updated: float


class DomainRateLimiter:
    def __init__(self, default_rps: float = 0.5, capacity: float = 2.0) -> None:
        self._default_rps = default_rps
        self._capacity = capacity
        self._buckets: dict[str, _Bucket] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _bucket(self, host: str) -> _Bucket:
        b = self._buckets.get(host)
        if b is None:
            now = time.monotonic()
            b = _Bucket(self._default_rps, self._capacity, self._capacity, now)
            self._buckets[host] = b
        return b

    async def acquire(self, host: str) -> None:
        async with self._locks[host]:
            b = self._bucket(host)
            while True:
                now = time.monotonic()
                elapsed = now - b.updated
                b.tokens = min(b.capacity, b.tokens + elapsed * b.rate)
                b.updated = now
                if b.tokens >= 1.0:
                    b.tokens -= 1.0
                    return
                # need more tokens; sleep just enough
                deficit = 1.0 - b.tokens
                await asyncio.sleep(deficit / b.rate)
