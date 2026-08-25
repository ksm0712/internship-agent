from __future__ import annotations

import time

from internship_agent.cache import TTLCache


class TestTTLCache:
    def test_miss_then_hit(self, db):
        cache = TTLCache(db)
        assert cache.get("k") is None
        cache.set("k", {"domain": "acme.com"})
        assert cache.get("k") == {"domain": "acme.com"}
        assert cache.hits == 1
        assert cache.misses == 1

    def test_overwrite_updates_value(self, db):
        cache = TTLCache(db)
        cache.set("k", "v1")
        cache.set("k", "v2")
        assert cache.get("k") == "v2"

    def test_expired_entry_is_a_miss_and_is_deleted(self, db):
        cache = TTLCache(db)
        cache.set("k", "v", ttl_seconds=0)
        time.sleep(0.01)
        assert cache.get("k") is None
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM cache_entries WHERE cache_key = 'k'")
            assert cur.fetchone()["n"] == 0

    def test_get_or_set_computes_once(self, db):
        cache = TTLCache(db)
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return "computed"

        assert cache.get_or_set("k", compute) == "computed"
        assert cache.get_or_set("k", compute) == "computed"
        assert calls["n"] == 1

    def test_hit_rate(self, db):
        cache = TTLCache(db)
        cache.get("missing")
        cache.set("k", "v")
        cache.get("k")
        cache.get("k")
        assert cache.hit_rate == 2 / 3

    def test_hit_rate_is_zero_with_no_activity(self, db):
        cache = TTLCache(db)
        assert cache.hit_rate == 0.0
