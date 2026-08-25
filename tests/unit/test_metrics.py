from __future__ import annotations

import time

import pytest

from internship_agent.metrics import StageTimer, aggregate_stats, new_run_id


class TestStageTimer:
    def test_records_a_row_with_counts_and_positive_duration(self, db):
        run_id = new_run_id()
        with StageTimer(db, run_id=run_id, stage="search", user_email="a@example.com") as timer:
            timer.items_in = 10
            timer.items_out = 4
            timer.record_api_call()
            timer.record_api_call()
            timer.record_cache_hit()
            time.sleep(0.001)

        with db.cursor() as cur:
            cur.execute("SELECT * FROM run_metrics WHERE run_id = ?", (run_id,))
            row = dict(cur.fetchone())

        assert row["stage"] == "search"
        assert row["items_in"] == 10
        assert row["items_out"] == 4
        assert row["api_calls"] == 2
        assert row["cache_hits"] == 1
        assert row["errors"] == 0
        assert row["duration_ms"] > 0
        assert row["user_email"] == "a@example.com"

    def test_records_error_on_exception_and_reraises(self, db):
        run_id = new_run_id()
        with pytest.raises(ValueError):
            with StageTimer(db, run_id=run_id, stage="draft"):
                raise ValueError("boom")

        with db.cursor() as cur:
            cur.execute("SELECT errors FROM run_metrics WHERE run_id = ?", (run_id,))
            assert cur.fetchone()["errors"] == 1

    def test_run_id_groups_multiple_stages(self, db):
        run_id = new_run_id()
        with StageTimer(db, run_id=run_id, stage="search"):
            pass
        with StageTimer(db, run_id=run_id, stage="contacts"):
            pass
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM run_metrics WHERE run_id = ?", (run_id,))
            assert cur.fetchone()["n"] == 2


class TestAggregateStats:
    def _record(self, db, stage, *, items_out=1, api_calls=1, cache_hits=0, errors=0, run_id=None):
        with StageTimer(db, run_id=run_id or new_run_id(), stage=stage) as timer:
            timer.items_out = items_out
            for _ in range(api_calls):
                timer.record_api_call()
            for _ in range(cache_hits):
                timer.record_cache_hit()
            timer.errors = errors

    def test_empty_database_has_no_stages(self, db):
        stats = aggregate_stats(db)
        assert stats["total_runs"] == 0
        assert stats["stages"] == {}

    def test_aggregates_counts_across_runs(self, db):
        self._record(db, "search", items_out=3, api_calls=2)
        self._record(db, "search", items_out=5, api_calls=1)
        stats = aggregate_stats(db)
        search = stats["stages"]["search"]
        assert search["runs"] == 2
        assert search["items_out_total"] == 8
        assert search["api_calls"] == 3

    def test_cache_hit_rate(self, db):
        self._record(db, "contacts", api_calls=3, cache_hits=1)
        stats = aggregate_stats(db)
        assert stats["stages"]["contacts"]["cache_hit_rate"] == pytest.approx(0.25)

    def test_error_rate(self, db):
        self._record(db, "draft", errors=1)
        self._record(db, "draft", errors=0)
        stats = aggregate_stats(db)
        assert stats["stages"]["draft"]["error_rate"] == pytest.approx(0.5)

    def test_filter_by_stage(self, db):
        self._record(db, "search")
        self._record(db, "draft")
        stats = aggregate_stats(db, stage="search")
        assert set(stats["stages"].keys()) == {"search"}

    def test_total_runs_counts_distinct_run_ids(self, db):
        run_id = new_run_id()
        self._record(db, "search", run_id=run_id)
        self._record(db, "contacts", run_id=run_id)
        self._record(db, "draft")
        stats = aggregate_stats(db)
        assert stats["total_runs"] == 2
        assert stats["total_stage_executions"] == 3
