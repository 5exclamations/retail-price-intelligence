"""Writers for orchestration telemetry: runs, steps, data-quality results and alerts."""

from __future__ import annotations

import json

import psycopg


def record_dq(
    conn: psycopg.Connection,
    *,
    run_id: str | None,
    layer: str,
    check: str,
    severity: str,
    passed: bool,
    scope: str = "all",
    observed: float | None = None,
    threshold: str | None = None,
    detail: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO ops.dq_result (run_id, layer, check_name, scope, severity, passed, observed, threshold, detail)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (run_id, layer, check, scope, severity, passed, observed, threshold, detail),
    )


def raise_alert(
    conn: psycopg.Connection,
    *,
    run_id: str | None,
    severity: str,
    title: str,
    source: str | None = None,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO ops.alert (run_id, severity, source, title, detail) VALUES (%s, %s, %s, %s, %s)",
        (run_id, severity, source, title, detail),
    )


def start_run(conn: psycopg.Connection, run_id: str, flow: str, as_of=None) -> None:
    conn.execute(
        "INSERT INTO ops.pipeline_run (run_id, flow_name, status, as_of_date) VALUES (%s, %s, 'running', %s)"
        " ON CONFLICT (run_id) DO NOTHING",
        (run_id, flow, as_of),
    )


def finish_run(
    conn: psycopg.Connection,
    run_id: str,
    status: str,
    stats: dict | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE ops.pipeline_run SET status = %s, finished_at = now(), stats = %s::jsonb, error = %s WHERE run_id = %s",
        (status, json.dumps(stats or {}, default=str), error, run_id),
    )


def record_step(
    conn: psycopg.Connection,
    run_id: str,
    step: str,
    status: str,
    *,
    attempts: int = 1,
    stats: dict | None = None,
    error: str | None = None,
    started_at=None,
) -> None:
    conn.execute(
        """INSERT INTO ops.step_run (run_id, step, status, attempts, stats, error, started_at, finished_at)
           VALUES (%s, %s, %s, %s, %s::jsonb, %s, COALESCE(%s, now()), now())""",
        (run_id, step, status, attempts, json.dumps(stats or {}, default=str), error, started_at),
    )
