"""The Prefect flow: end-to-end run, idempotent re-run, incident handling, retries and failure reporting."""

from __future__ import annotations

import json
from datetime import date

import pytest

from rpi.flows import pipeline, steps
from rpi.tests.conftest import connect, generate_feeds

pytestmark = pytest.mark.slow

END = date(2026, 9, 30)


@pytest.fixture(autouse=True)
def _reports(tmp_path, monkeypatch):
    monkeypatch.setenv("RPI_REPORTS_DIR", str(tmp_path / "reports"))


def _runs(url):
    with connect(url) as c:
        return c.execute("SELECT * FROM ops.pipeline_run ORDER BY started_at").fetchall()


def test_full_run_then_idempotent_rerun(fresh_db):
    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=14, products=60)
    first = pipeline.daily_pipeline(as_of=END, landing_dir=str(fresh_db["landing"]))
    assert first["status"] == "succeeded", first
    assert first["steps"]["ingest"]["files_ingested"] > 60
    assert first["steps"]["dbt"]["tests_failed"] == 0

    with connect(fresh_db["url"]) as c:
        steps_ = c.execute(
            "SELECT step, status FROM ops.step_run WHERE run_id = %s ORDER BY id",
            (first["run_id"],),
        ).fetchall()
        counts = c.execute(
            "SELECT (SELECT count(*) FROM silver.price_observation) AS obs, (SELECT count(*) FROM gold.fct_price_daily) AS fct"
        ).fetchone()
    assert [s["step"] for s in steps_] == [
        "ingest_bronze",
        "build_silver",
        "match_products",
        "data_quality",
        "dbt_build",
    ]
    assert all(s["status"] == "succeeded" for s in steps_)

    second = pipeline.daily_pipeline(as_of=END, landing_dir=str(fresh_db["landing"]))
    assert second["status"] == "succeeded"
    assert (
        second["steps"]["ingest"]["files_ingested"] == 0
        and second["steps"]["silver"]["observations_inserted"] == 0
    )
    with connect(fresh_db["url"]) as c:
        assert (
            c.execute(
                "SELECT (SELECT count(*) FROM silver.price_observation) AS obs, (SELECT count(*) FROM gold.fct_price_daily) AS fct"
            ).fetchone()
            == counts
        )


def test_a_bad_feed_degrades_the_run_but_does_not_stop_it(fresh_db):
    generate_feeds(
        fresh_db["landing"],
        fresh_db["truth"],
        days=10,
        products=50,
        clean=True,
        incidents=["unit_mismatch:caspianmart:2026-09-30"],
    )
    run = pipeline.daily_pipeline(as_of=END, landing_dir=str(fresh_db["landing"]), run_dbt=False)
    assert run["status"] == "degraded"
    assert run["steps"]["silver"]["rejected_batches"] == [
        {"source": "caspianmart", "reason": "median_price_out_of_range"}
    ]
    with connect(fresh_db["url"]) as c:
        alert = c.execute("SELECT * FROM ops.alert WHERE run_id = %s", (run["run_id"],)).fetchone()
        stale = c.execute(
            "SELECT * FROM ops.dq_result WHERE run_id = %s AND check_name = 'freshness_days' AND scope = 'caspianmart'",
            (run["run_id"],),
        ).fetchone()
    assert alert["severity"] == "error" and alert["source"] == "caspianmart"
    assert (
        stale["observed"] == 1.0 and not stale["passed"]
    )  # the missing day is visible as a stale feed


def test_a_failing_step_fails_the_run_and_writes_a_report(fresh_db, monkeypatch, tmp_path):
    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=3, products=30, clean=True)

    def boom(*a, **k):
        raise RuntimeError("silver exploded")

    monkeypatch.setattr(steps, "silver_step", boom)
    monkeypatch.setattr(pipeline, "silver_task", pipeline.silver_task.with_options(retries=0))
    with pytest.raises(RuntimeError, match="silver exploded"):
        pipeline.daily_pipeline(as_of=END, landing_dir=str(fresh_db["landing"]), run_dbt=False)

    run = _runs(fresh_db["url"])[-1]
    assert run["status"] == "failed" and "silver exploded" in run["error"]
    with connect(fresh_db["url"]) as c:
        step = c.execute(
            "SELECT * FROM ops.step_run WHERE run_id = %s AND status = 'failed'", (run["run_id"],)
        ).fetchone()
        alert = c.execute(
            "SELECT * FROM ops.alert WHERE run_id = %s AND title = 'pipeline failed'",
            (run["run_id"],),
        ).fetchone()
    assert step["step"] == "build_silver" and alert
    report = json.loads((tmp_path / "reports" / f"failure_{run['run_id']}.json").read_text())
    assert report["error"].startswith("RuntimeError") and [s["step"] for s in report["steps"]] == [
        "ingest_bronze",
        "build_silver",
    ]
    assert "Traceback" in report["traceback"]


def test_a_transient_failure_is_retried_and_the_run_succeeds(fresh_db, monkeypatch):
    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=3, products=30, clean=True)
    calls = {"n": 0}
    real = steps.ingest_step

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("network blip")
        return real(*a, **k)

    monkeypatch.setattr(steps, "ingest_step", flaky)
    monkeypatch.setattr(
        pipeline, "ingest_task", pipeline.ingest_task.with_options(retries=2, retry_delay_seconds=0)
    )
    run = pipeline.daily_pipeline(as_of=END, landing_dir=str(fresh_db["landing"]), run_dbt=False)
    assert run["status"] in ("succeeded", "degraded") and calls["n"] == 2
    assert run["steps"]["ingest"]["files_ingested"] > 0


def test_a_crash_midway_resumes_without_duplicates(fresh_db, monkeypatch):
    """Bronze is loaded, silver dies: the next run must pick up the pending batches and finish."""
    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=5, products=40, clean=True)
    monkeypatch.setattr(
        steps, "silver_step", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    monkeypatch.setattr(pipeline, "silver_task", pipeline.silver_task.with_options(retries=0))
    with pytest.raises(RuntimeError):
        pipeline.daily_pipeline(as_of=END, landing_dir=str(fresh_db["landing"]), run_dbt=False)
    monkeypatch.undo()
    monkeypatch.setenv("RPI_REPORTS_DIR", str(fresh_db["landing"].parent / "r2"))
    run = pipeline.daily_pipeline(as_of=END, landing_dir=str(fresh_db["landing"]), run_dbt=False)
    assert run["status"] in ("succeeded", "degraded")
    assert run["steps"]["ingest"]["files_ingested"] == 0  # bronze was already complete
    assert run["steps"]["silver"]["batches"] > 0 and run["steps"]["silver"]["batches_failed"] == 0
    with connect(fresh_db["url"]) as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM bronze.ingest_batch WHERE status <> 'silver_done'"
            ).fetchone()["n"]
            == 0
        )
