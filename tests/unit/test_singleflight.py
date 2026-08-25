from __future__ import annotations

import threading
import time

from internship_agent.singleflight import SingleFlight


class TestSingleFlight:
    def test_returns_compute_result(self):
        sf = SingleFlight()
        assert sf.do("k", lambda: 42) == 42

    def test_different_keys_do_not_block_each_other(self):
        sf = SingleFlight()
        assert sf.do("a", lambda: "a-result") == "a-result"
        assert sf.do("b", lambda: "b-result") == "b-result"

    def test_concurrent_calls_for_the_same_key_are_serialized(self):
        sf = SingleFlight()
        concurrent_count = {"current": 0, "max": 0}
        lock = threading.Lock()

        def compute():
            with lock:
                concurrent_count["current"] += 1
                concurrent_count["max"] = max(concurrent_count["max"], concurrent_count["current"])
            time.sleep(0.02)
            with lock:
                concurrent_count["current"] -= 1
            return "done"

        threads = [threading.Thread(target=lambda: sf.do("shared-key", compute)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert concurrent_count["max"] == 1

    def test_calls_for_different_keys_can_run_concurrently(self):
        sf = SingleFlight()
        concurrent_count = {"current": 0, "max": 0}
        lock = threading.Lock()
        barrier = threading.Barrier(4, timeout=5)

        def compute():
            barrier.wait()
            with lock:
                concurrent_count["current"] += 1
                concurrent_count["max"] = max(concurrent_count["max"], concurrent_count["current"])
            time.sleep(0.02)
            with lock:
                concurrent_count["current"] -= 1

        threads = [threading.Thread(target=lambda i=i: sf.do(f"key-{i}", compute)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert concurrent_count["max"] > 1
