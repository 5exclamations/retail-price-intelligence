"""Prefect flow: landing files -> bronze -> silver -> matching -> data quality -> dbt (gold).

Design notes
* Steps are idempotent, so Prefect task retries (and re-running the whole flow) are always safe.
* Each task also writes a row to ops.step_run, so run history is queryable from SQL and shown in the
  dashboard without a Prefect server.
* Failure reporting: an exception marks the run 'failed', raises an alert and writes a report file.
  Data problems that did not stop the flow (rejected batches, error-level checks) mark the run 'degraded'.
"""

from __future__ import annotations

import json
import traceback
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from prefect import flow, get_run_logger, task

from rpi import logging_setup, ops
from rpi.config import get_settings
from rpi.db import connect
from rpi.flows import steps
from rpi.migrate import migrate
from rpi.warehouse import dbt

log = logging_setup.get(__name__)


def _ledger(run_id: str, step: str, fn, *args, retries_seen: int = 1, **kwargs) -> dict:
    started = datetime.now(UTC)
    try:
        stats = fn(*args, **kwargs)
    except Exception as exc:
        with connect() as conn:
            ops.record_step(
                conn,
                run_id,
                step,
                "failed",
                attempts=retries_seen,
                error=f"{type(exc).__name__}: {exc}"[:800],
                started_at=started,
            )
            conn.commit()
        raise
    with connect() as conn:
        ops.record_step(
            conn, run_id, step, "succeeded", attempts=retries_seen, stats=stats, started_at=started
        )
        conn.commit()
    return stats


@task(name="ingest-bronze", retries=3, retry_delay_seconds=[1, 5, 15])
def ingest_task(run_id: str, landing_dir: str | None, up_to: date | None) -> dict:
    return _ledger(
        run_id,
        "ingest_bronze",
        steps.ingest_step,
        Path(landing_dir) if landing_dir else None,
        up_to=up_to,
    )


@task(name="build-silver", retries=2, retry_delay_seconds=[2, 10])
def silver_task(run_id: str) -> dict:
    return _ledger(run_id, "build_silver", steps.silver_step, run_id)


@task(name="match-products", retries=1, retry_delay_seconds=2)
def match_task(run_id: str) -> dict:
    return _ledger(run_id, "match_products", steps.match_step)


@task(name="data-quality", retries=1, retry_delay_seconds=2)
def dq_task(run_id: str, as_of: date) -> dict:
    return _ledger(run_id, "data_quality", steps.dq_step, run_id, as_of)


@task(name="dbt-build", retries=1, retry_delay_seconds=5)
def dbt_task(run_id: str) -> dict:
    def run() -> dict:
        dbt("snapshot")
        summary = dbt("build")
        steps.record_dbt_results(run_id, summary)
        return summary.as_dict() | {"failures": len(summary.failures)}

    return _ledger(run_id, "dbt_build", run)


def _write_failure_report(run_id: str, error: str, trace: str) -> Path:
    out = get_settings().reports_dir
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"failure_{run_id}.json"
    with connect() as conn:
        steps_done = conn.execute(
            "SELECT step, status, error FROM ops.step_run WHERE run_id = %s ORDER BY id", (run_id,)
        ).fetchall()
        alerts = conn.execute(
            "SELECT severity, source, title, detail FROM ops.alert WHERE run_id = %s ORDER BY id",
            (run_id,),
        ).fetchall()
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "error": error,
                "traceback": trace,
                "steps": steps_done,
                "alerts": alerts,
            },
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    return path


def _finish(run_id: str, status: str, stats: dict, error: str | None = None) -> None:
    with connect() as conn:
        ops.finish_run(conn, run_id, status, stats, error)
        conn.commit()


@flow(name="rpi-daily-pipeline", log_prints=False)
def daily_pipeline(
    as_of: date | None = None,
    landing_dir: str | None = None,
    run_dbt: bool = True,
    up_to: date | None = None,
    run_id: str | None = None,
) -> dict:
    """Run the full pipeline once. Returns {'run_id', 'status', 'steps': {...}}."""
    logging_setup.configure()
    run_id = run_id or f"run-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    logging_setup.bind(run_id=run_id)
    migrate()
    as_of = as_of or date.today()
    with connect() as conn:
        ops.start_run(conn, run_id, "rpi-daily-pipeline", as_of)
        conn.commit()
    plog = get_run_logger()
    plog.info("pipeline started run_id=%s as_of=%s", run_id, as_of)
    result: dict = {}
    try:
        result["ingest"] = ingest_task(run_id, landing_dir, up_to)
        result["silver"] = silver_task(run_id)
        result["match"] = match_task(run_id)
        result["dq"] = dq_task(run_id, as_of)
        if run_dbt:
            result["dbt"] = dbt_task(run_id)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        trace = traceback.format_exc()
        with connect() as conn:
            ops.raise_alert(
                conn, run_id=run_id, severity="error", title="pipeline failed", detail=error[:800]
            )
            conn.commit()
        report = _write_failure_report(run_id, error, trace)
        log.error("pipeline failed", extra={"error": error, "report": str(report)})
        _finish(run_id, "failed", result, error)
        raise

    with connect() as conn:
        errors = conn.execute(
            "SELECT count(*) AS n FROM ops.alert WHERE run_id = %s AND severity = 'error'",
            (run_id,),
        ).fetchone()["n"]
    degraded = (
        errors > 0 or result["dq"]["failed_errors"] > 0 or result["silver"]["batches_failed"] > 0
    )
    status = "degraded" if degraded else "succeeded"
    _finish(run_id, status, result)
    log.info("pipeline finished", extra={"status": status, "alerts": errors})
    return {"run_id": run_id, "status": status, "steps": result}
