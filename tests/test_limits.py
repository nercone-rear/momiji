from momiji import ConnectionTracker, RateLimiter
from momiji.limits import TokenBucket

def test_connection_tracker_allows_up_to_max_connections():
    tracker = ConnectionTracker(max_connections=2)

    assert tracker.try_acquire("a") is True
    assert tracker.try_acquire("b") is True
    assert tracker.try_acquire("c") is False
    assert len(tracker.active) == 2

def test_connection_tracker_release_frees_a_slot():
    tracker = ConnectionTracker(max_connections=1)

    assert tracker.try_acquire("a") is True
    assert tracker.try_acquire("b") is False

    tracker.release("a")

    assert tracker.try_acquire("b") is True

def test_connection_tracker_unbounded_when_max_connections_is_none():
    tracker = ConnectionTracker(max_connections=None)

    for i in range(1000):
        assert tracker.try_acquire(i) is True

def test_connection_tracker_release_of_untracked_connection_is_a_noop():
    tracker = ConnectionTracker(max_connections=1)
    tracker.release("never-acquired")
    assert tracker.active == set()

def test_token_bucket_allows_burst_up_to_capacity():
    bucket = TokenBucket(rate=1, capacity=3)

    assert bucket.allow() is True
    assert bucket.allow() is True
    assert bucket.allow() is True
    assert bucket.allow() is False

def test_token_bucket_refills_over_time(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("momiji.limits.time.monotonic", lambda: now[0])

    bucket = TokenBucket(rate=2, capacity=1)
    assert bucket.allow() is True
    assert bucket.allow() is False

    now[0] += 0.5

    assert bucket.allow() is True
    assert bucket.allow() is False

def test_rate_limiter_tracks_keys_independently():
    limiter = RateLimiter(rate=0, burst=1)

    assert limiter.allow("1.1.1.1") is True
    assert limiter.allow("1.1.1.1") is False
    assert limiter.allow("2.2.2.2") is True

def test_rate_limiter_prune_removes_stale_buckets(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("momiji.limits.time.monotonic", lambda: now[0])

    limiter = RateLimiter(rate=1, burst=1)
    limiter.allow("stale")

    now[0] += 100
    limiter.allow("fresh")

    limiter.prune(max_idle=50)

    assert "stale" not in limiter.buckets
    assert "fresh" in limiter.buckets
