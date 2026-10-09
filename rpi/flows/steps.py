"""Pipeline steps as plain functions. The Prefect flow wraps them as tasks; tests and the CLI call them directly.

Every step opens its own connection and transaction, is idempotent, and returns a stats dict.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from rpi import logging_setup, ops
from rpi.config import get_settings
from rpi.db import connect
from rpi.dq.checks import run_checks
from rpi.ingest.bronze import discover, ingest_file
from rpi.matching.matcher import Matcher, refresh_quarantine
from rpi.silver.transform import process_pending

log = logging_setup.get(__name__)


def ingest_step(landing_dir: Path | None = None, *, up_to: date | None = None) -> dict:
    landing = landing_dir or get_settings().landing_dir
    files = discover(landing, up_to=up_to)
    stats = {
        "files_found": len(files),
        "files_ingested": 0,
        "files_skipped": 0,
        "rows": 0,
        "batch_ids": [],
    }
    with connect() as conn:
        for f in files:
            res = ingest_file(conn, f)
            conn.commit()  # one transaction per file: a crash loses at most the file in flight
            if res["status"] == "ingested":
                stats["files_ingested"] += 1
                stats["rows"] += res["rows"]
                stats["batch_ids"].append(res["batch_id"])
            else:
                stats["files_skipped"] += 1
    return stats


def silver_step(run_id: str | None = None) -> dict:
    with connect() as conn:
        results = process_pending(conn, run_id=run_id)
    done = [r for r in results if r["status"] == "silver_done"]
    return {
        "batches": len(results),
        "batches_rejected": sum(1 for r in results if r["status"] == "rejected"),
        "batches_failed": sum(1 for r in results if r["status"] == "failed"),
        "observations_inserted": sum(r.get("observations_inserted", 0) for r in done),
        "rows_rejected": sum(r.get("rejected", 0) for r in done),
        "rejected_batches": [
            {"source": r["source"], "reason": r.get("reason")}
            for r in results
            if r["status"] == "rejected"
        ],
    }


def match_step() -> dict:
    with connect() as conn:
        stats = Matcher(conn).run()
        quarantine = refresh_quarantine(conn)
        conn.commit()
        pending = conn.execute(
            "SELECT count(*) AS n FROM silver.match_review WHERE status = 'pending'"
        ).fetchone()["n"]
    return {**vars(stats), "quarantined_products": quarantine, "review_pending": pending}


def dq_step(run_id: str | None, as_of: date) -> dict:
    with connect() as conn:
        results = run_checks(conn, run_id=run_id, as_of=as_of)
        conn.commit()
    failed = [r for r in results if not r.passed]
    return {
        "checks": len(results),
        "failed_errors": sum(1 for r in failed if r.severity == "error"),
        "failed_warnings": sum(1 for r in failed if r.severity == "warn"),
        "failures": [
            {"check": r.check, "scope": r.scope, "severity": r.severity, "observed": r.observed}
            for r in failed
        ],
    }


def record_dbt_results(run_id: str | None, summary) -> None:
    with connect() as conn:
        ops.record_dq(
            conn,
            run_id=run_id,
            layer="gold",
            check="dbt_tests",
            severity="error",
            passed=summary.tests_failed == 0,
            observed=float(summary.tests_failed),
            threshold="0 failing tests",
            detail=f"{summary.tests_passed} passed, {summary.tests_failed} failed",
        )
        conn.commit()
