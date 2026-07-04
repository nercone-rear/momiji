import time

from momiji.limits import ConnectionTracker, TokenBucket, RateLimiter


class FakeProtocol:
    """Stand-in for Protocol; ConnectionTracker only needs a hashable object."""


class TestConnectionTracker:
    def test_unbounded_tracker_always_acquires(self):
        tracker = ConnectionTracker(None)
        for _ in range(1000):
            assert tracker.try_acquire(FakeProtocol())

    def test_bounded_tracker_rejects_beyond_capacity(self):
        tracker = ConnectionTracker(2)
        a, b, c = FakeProtocol(), FakeProtocol(), FakeProtocol()
        assert tracker.try_acquire(a)
        assert tracker.try_acquire(b)
        assert not tracker.try_acquire(c)

    def test_release_frees_capacity(self):
        tracker = ConnectionTracker(1)
        a, b = FakeProtocol(), FakeProtocol()
        assert tracker.try_acquire(a)
        assert not tracker.try_acquire(b)
        tracker.release(a)
        assert tracker.try_acquire(b)

    def test_release_of_untracked_protocol_is_noop(self):
        tracker = ConnectionTracker(1)
        tracker.release(FakeProtocol())  # must not raise

    def test_active_set_reflects_acquired_protocols(self):
        tracker = ConnectionTracker(None)
        a = FakeProtocol()
        tracker.try_acquire(a)
        assert a in tracker.active
        tracker.release(a)
        assert a not in tracker.active

    def test_shutting_down_defaults_false(self):
        assert ConnectionTracker(None).shutting_down is False


class TestTokenBucket:
    def test_allows_up_to_capacity_immediately(self):
        bucket = TokenBucket(rate=1, capacity=3)
        assert bucket.allow()
        assert bucket.allow()
        assert bucket.allow()
        assert not bucket.allow()

    def test_refills_over_time(self):
        bucket = TokenBucket(rate=1000, capacity=1)
        assert bucket.allow()
        assert not bucket.allow()
        time.sleep(0.01)
        assert bucket.allow()

    def test_tokens_never_exceed_capacity(self):
        bucket = TokenBucket(rate=1000, capacity=2)
        time.sleep(0.01)
        assert bucket.allow()
        assert bucket.allow()
        assert not bucket.allow()

    def test_cost_greater_than_one(self):
        bucket = TokenBucket(rate=0, capacity=5)
        assert bucket.allow(cost=5)
        assert not bucket.allow(cost=1)

    def test_last_used_updates_on_allow(self):
        bucket = TokenBucket(rate=1, capacity=1)
        before = bucket.last_used
        time.sleep(0.01)
        bucket.allow()
        assert bucket.last_used > before


class TestRateLimiter:
    def test_allows_within_burst_per_key(self):
        limiter = RateLimiter(rate=1, burst=2)
        assert limiter.allow("a")
        assert limiter.allow("a")
        assert not limiter.allow("a")

    def test_keys_are_independent(self):
        limiter = RateLimiter(rate=1, burst=1)
        assert limiter.allow("a")
        assert limiter.allow("b")
        assert not limiter.allow("a")
        assert not limiter.allow("b")

    def test_prune_removes_stale_buckets(self):
        limiter = RateLimiter(rate=1, burst=1)
        limiter.allow("a")
        limiter.buckets["a"].last_used = time.monotonic() - 1000
        limiter.prune(max_idle=1)
        assert "a" not in limiter.buckets

    def test_prune_keeps_recently_used_buckets(self):
        limiter = RateLimiter(rate=1, burst=1)
        limiter.allow("a")
        limiter.prune(max_idle=300)
        assert "a" in limiter.buckets
