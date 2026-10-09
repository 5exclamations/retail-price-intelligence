"""Data-quality checks: they must pass on healthy data and fail loudly on each kind of damage."""

from __future__ import annotations

from datetime import date

import pytest

from rpi.dq.checks import run_checks
from rpi.flows import steps
from rpi.tests.conftest import connect, generate_feeds

pytestmark = pytest.mark.slow


def _by_name(results, name):
    return [r for r in results if r.check == name]


def test_healthy_data_has_no_error_level_failures(built_db):
    assert built_db["dq"]["failed_errors"] == 0
    assert built_db["dq"]["checks"] >= 25


def test_results_are_persisted_for_the_run(db):
    n = db.execute("SELECT count(*) AS n FROM ops.dq_result WHERE run_id = 'test-run'").fetchone()[
        "n"
    ]
    layers = {
        r["layer"]
        for r in db.execute("SELECT DISTINCT layer FROM ops.dq_result WHERE run_id = 'test-run'")
    }
    assert n >= 25 and layers == {"bronze", "silver"}


def test_stale_data_fails_freshness_with_error_severity(db):
    results = run_checks(
        db, run_id=None, as_of=date(2026, 10, 10)
    )  # ten days after the last observation
    fresh = _by_name(results, "freshness_days")
    assert len(fresh) == 5 and all(
        not r.passed and r.severity == "error" and r.observed == 10.0 for r in fresh
    )
    db.rollback()


def test_one_day_lag_is_only_a_warning(db):
    results = run_checks(db, run_id=None, as_of=date(2026, 10, 2))
    fresh = _by_name(results, "freshness_days")
    assert all(not r.passed and r.severity == "warn" for r in fresh)
    db.rollback()


def test_reconciliation_catches_rows_that_vanish(fresh_db):
    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=4, products=40)
    steps.ingest_step()
    steps.silver_step()  # noqa: E702
    with connect(fresh_db["url"]) as c:
        ok = _by_name(run_checks(c, run_id=None, as_of=date(2026, 9, 30)), "row_reconciliation")[0]
        assert ok.passed
        # Someone claims a bronze file had more rows than silver accounts for.
        c.execute(
            "UPDATE bronze.ingest_batch SET row_count = row_count + 7 WHERE batch_id = (SELECT min(batch_id) FROM bronze.ingest_batch WHERE kind = 'prices')"
        )
        bad = _by_name(run_checks(c, run_id=None, as_of=date(2026, 9, 30)), "row_reconciliation")[0]
    assert not bad.passed and bad.severity == "error" and bad.observed == 1.0


def test_stuck_batches_are_reported(fresh_db):
    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=2, products=30)
    steps.ingest_step()  # silver step deliberately not run
    with connect(fresh_db["url"]) as c:
        r = _by_name(run_checks(c, run_id=None, as_of=date(2026, 9, 30)), "no_stuck_batches")[0]
    assert not r.passed and r.observed > 0


def test_implausible_price_median_is_an_error(fresh_db):
    """A retailer whose silver prices are all 100x too high must trip the range check, not just the batch gate."""
    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=3, products=40, clean=True)
    steps.ingest_step()
    steps.silver_step()  # noqa: E702
    with connect(fresh_db["url"]) as c:
        # Append-only: simulate by loosening the gate threshold instead of touching history.
        from rpi.config import Settings

        results = run_checks(
            c, run_id=None, as_of=date(2026, 9, 30), settings=Settings(median_price_max=100)
        )
    bad = _by_name(results, "median_price_in_range")
    assert bad and all(not r.passed and r.severity == "error" for r in bad)
