"""Pipeline run metrics: per-stage timing/throughput, persisted to SQLite.

`StageTimer` records start/end/counts for one stage run to `run_metrics`;
`aggregate_stats()` rolls those rows into the numbers on `/api/stats`.
"""
from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from internship_agent.db import Database


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class StageTimer:
    """Context manager that times one pipeline stage and records it on exit.

    Usage:
        with StageTimer(db, run_id="abc123", stage="search") as timer:
            timer.items_in = len(queries)
            ... do work, calling timer.record_api_call() / record_cache_hit() ...
            timer.items_out = len(results)
    """

    db: Database
    run_id: str
    stage: str
    user_email: str | None = None
    items_in: int = 0
    items_out: int = 0
    api_calls: int = field(default=0, init=False)
    cache_hits: int = field(default=0, init=False)
    errors: int = field(default=0, init=False)
    _started_monotonic: float = field(default=0.0, init=False, repr=False)
    _started_at: str = field(default="", init=False, repr=False)

    def __enter__(self) -> StageTimer:
        self._started_monotonic = time.monotonic()
        self._started_at = datetime.now(UTC).isoformat()
        return self

    def record_api_call(self) -> None:
        self.api_calls += 1

    def record_cache_hit(self) -> None:
        self.cache_hits += 1

    def record_error(self) -> None:
        self.errors += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None:
            self.errors += 1
        duration_ms = (time.monotonic() - self._started_monotonic) * 1000
        finished_at = datetime.now(UTC).isoformat()
        with self.db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_metrics
                    (run_id, stage, started_at, finished_at, duration_ms,
                     items_in, items_out, api_calls, cache_hits, errors, user_email)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    self.stage,
                    self._started_at,
                    finished_at,
                    duration_ms,
                    self.items_in,
                    self.items_out,
                    self.api_calls,
                    self.cache_hits,
                    self.errors,
                    self.user_email,
                ),
            )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))
    return ordered[index]


def aggregate_stats(db: Database, *, stage: str | None = None) -> dict[str, Any]:
    """Roll up recorded run_metrics rows into summary stats for the dashboard."""
    with db.cursor() as cur:
        if stage:
            cur.execute("SELECT * FROM run_metrics WHERE stage = ?", (stage,))
        else:
            cur.execute("SELECT * FROM run_metrics")
        rows = [dict(row) for row in cur.fetchall()]

    by_stage: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_stage.setdefault(row["stage"], []).append(row)

    stages: dict[str, Any] = {}
    for name, stage_rows in by_stage.items():
        durations = [r["duration_ms"] for r in stage_rows]
        api_calls = sum(r["api_calls"] for r in stage_rows)
        cache_hits = sum(r["cache_hits"] for r in stage_rows)
        errors = sum(r["errors"] for r in stage_rows)
        stages[name] = {
            "runs": len(stage_rows),
            "items_out_total": sum(r["items_out"] for r in stage_rows),
            "latency_ms_p50": round(_percentile(durations, 0.50), 1),
            "latency_ms_p95": round(_percentile(durations, 0.95), 1),
            "latency_ms_avg": round(statistics.fmean(durations), 1) if durations else 0.0,
            "api_calls": api_calls,
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / (api_calls + cache_hits), 3)
            if (api_calls + cache_hits)
            else 0.0,
            "errors": errors,
            "error_rate": round(errors / len(stage_rows), 3) if stage_rows else 0.0,
        }

    return {
        "total_runs": len({r["run_id"] for r in rows}),
        "total_stage_executions": len(rows),
        "stages": stages,
    }
