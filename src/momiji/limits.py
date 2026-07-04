from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import Protocol

class ConnectionTracker:
    def __init__(self, max_connections: Optional[int] = None):
        self.max_connections = max_connections
        self.active: set["Protocol"] = set()
        self.shutting_down = False

    def try_acquire(self, protocol: "Protocol") -> bool:
        if self.max_connections is not None and len(self.active) >= self.max_connections:
            return False

        self.active.add(protocol)
        return True

    def release(self, protocol: "Protocol"):
        self.active.discard(protocol)

class TokenBucket:
    def __init__(self, rate: float, capacity: float):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self.last_used = self.last_refill

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.last_refill = now
        self.last_used = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

        if self.tokens < cost:
            return False

        self.tokens -= cost
        return True

class RateLimiter:
    def __init__(self, rate: float, burst: float):
        self.rate = rate
        self.burst = burst
        self.buckets: dict[str, TokenBucket] = {}

    def allow(self, key: str) -> bool:
        bucket = self.buckets.get(key)

        if bucket is None:
            bucket = TokenBucket(self.rate, self.burst)
            self.buckets[key] = bucket

        return bucket.allow()

    def prune(self, max_idle: float):
        now = time.monotonic()
        stale = [key for key, bucket in self.buckets.items() if now - bucket.last_used > max_idle]

        for key in stale:
            del self.buckets[key]
