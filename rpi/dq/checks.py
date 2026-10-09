"""Data-quality checks on the bronze and silver layers.

Gold is covered by dbt tests (see warehouse/); their outcome is folded into the same result table by the
flow so one query answers "is the data healthy?".

Every check returns a Result with a severity. ``error`` means consumers may be shown wrong numbers and
the run is marked degraded; ``warn`` is a signal to look at. Results are persisted in ops.dq_result.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date

import psycopg

from rpi import logging_setup, ops

log = logging_setup.get(__name__)


@dataclass
class Result:
    layer: str
    check: str
    severity: str
    passed: bool
    scope: str = "all"
    observed: float | None = None
    threshold: str | None = None
    detail: str | None = None


def _freshness(conn, as_of: date) -> list[Result]:
    out = []
    rows = conn.execute(
        "SELECT r.code, max(o.observed_date) AS last FROM silver.retailer r "
        "LEFT JOIN silver.store_item i ON i.retailer_code = r.code "
        "LEFT JOIN silver.price_observation o ON o.store_item_id = i.id GROUP BY r.code ORDER BY r.code"
    ).fetchall()
    for r in rows:
        lag = (as_of - r["last"]).days if r["last"] else 9999
        out.append(
            Result(
                "silver",
                "freshness_days",
                "error" if lag > 3 else "warn",
                lag <= 0,
                r["code"],
                float(lag),
                "0 days (a file for the as-of date)",
                f"last observation {r['last']}, as of {as_of}",
            )
        )
    return out


def _row_count_drift(conn, as_of: date) -> list[Result]:
    out = []
    for r in conn.execute(
        "SELECT DISTINCT source FROM bronze.ingest_batch WHERE kind = 'prices' ORDER BY 1"
    ).fetchall():
        rows = conn.execute(
            """SELECT business_date, row_count FROM bronze.ingest_batch
               WHERE source = %s AND kind = 'prices' AND status IN ('silver_done', 'rejected')
               ORDER BY business_date DESC LIMIT 8""",
            (r["source"],),
        ).fetchall()
        if len(rows) < 4:
            continue
        latest, history = rows[0]["row_count"], [x["row_count"] for x in rows[1:]]
        ratio = latest / statistics.median(history) if history else 1.0
        out.append(
            Result(
                "bronze",
                "row_count_vs_trailing_median",
                "warn",
                ratio >= 0.7,
                r["source"],
                round(ratio, 3),
                ">= 0.70",
                f"{latest} rows on {rows[0]['business_date']} vs median {statistics.median(history):.0f}",
            )
        )
    return out


def _price_range(conn, settings) -> list[Result]:
    out = []
    rows = conn.execute(
        """SELECT i.retailer_code, percentile_cont(0.5) WITHIN GROUP (ORDER BY o.price_qepik) AS med
           FROM silver.price_observation o JOIN silver.store_item i ON i.id = o.store_item_id
           WHERE o.observed_date = (SELECT max(observed_date) FROM silver.price_observation)
           GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    for r in rows:
        ok = settings.median_price_min <= r["med"] <= settings.median_price_max
        out.append(
            Result(
                "silver",
                "median_price_in_range",
                "error",
                ok,
                r["retailer_code"],
                float(r["med"]),
                f"{settings.median_price_min}-{settings.median_price_max} qepik",
                "a store whose median price is far outside the Baku range is almost always a wrong feed",
            )
        )
    return out


def run_checks(
    conn: psycopg.Connection, *, run_id: str | None, as_of: date, settings=None
) -> list[Result]:
    from rpi.config import get_settings

    settings = settings or get_settings()
    results: list[Result] = []

    # --- bronze ------------------------------------------------------------------------------
    unexplained = conn.execute(
        """WITH per AS (
               SELECT b.batch_id, b.row_count,
                      (SELECT count(*) FROM silver.price_observation o WHERE o.batch_id = b.batch_id) AS obs,
                      (SELECT count(*) FROM silver.rejected_record x WHERE x.batch_id = b.batch_id) AS rej,
                      (SELECT count(*) FROM bronze.ingest_batch c WHERE c.source = b.source AND c.kind = b.kind
                          AND c.business_date = b.business_date) AS same_day_batches
               FROM bronze.ingest_batch b WHERE b.status = 'silver_done' AND b.kind = 'prices')
           SELECT count(*) AS n FROM per WHERE same_day_batches = 1 AND row_count <> obs + rej"""
    ).fetchone()["n"]
    results.append(
        Result(
            "bronze",
            "row_reconciliation",
            "error",
            unexplained == 0,
            "all",
            float(unexplained),
            "0 batches",
            "rows in a bronze file must equal observations + rejected rows in silver",
        )
    )
    stuck = conn.execute(
        "SELECT count(*) AS n FROM bronze.ingest_batch WHERE status IN ('ingested', 'failed')"
    ).fetchone()["n"]
    results.append(
        Result(
            "bronze",
            "no_stuck_batches",
            "error",
            stuck == 0,
            "all",
            float(stuck),
            "0 batches",
            "batches still 'ingested' or 'failed' after the silver step",
        )
    )
    results += _row_count_drift(conn, as_of)

    # --- silver ------------------------------------------------------------------------------
    results += _freshness(conn, as_of)
    results += _price_range(conn, settings)

    dup = conn.execute(
        """SELECT count(*) AS n FROM (SELECT 1 FROM silver.price_observation
           GROUP BY store_item_id, COALESCE(store_id, 0), observed_date HAVING count(*) > 1) d"""
    ).fetchone()["n"]
    results.append(
        Result("silver", "no_duplicate_observations", "error", dup == 0, "all", float(dup), "0")
    )

    rej = conn.execute(
        """SELECT b.source, b.row_count, count(x.id) AS rej FROM bronze.ingest_batch b
           LEFT JOIN silver.rejected_record x ON x.batch_id = b.batch_id
           WHERE b.kind = 'prices' AND b.status = 'silver_done'
             AND b.business_date = (SELECT max(business_date) FROM bronze.ingest_batch WHERE kind = 'prices')
           GROUP BY b.source, b.row_count"""
    ).fetchall()
    for r in rej:
        share = r["rej"] / r["row_count"] if r["row_count"] else 0
        results.append(
            Result(
                "silver",
                "reject_ratio_latest_day",
                "warn",
                share <= 0.05,
                r["source"],
                round(share, 4),
                "<= 5%",
            )
        )

    lag = conn.execute(
        """WITH z AS (
               SELECT o.store_item_id, s.price_zone, o.observed_date, count(DISTINCT o.price_qepik) AS n
               FROM silver.price_observation o JOIN silver.store s ON s.id = o.store_id
               WHERE o.observed_date > (SELECT max(observed_date) FROM silver.price_observation) - 7
               GROUP BY 1, 2, 3)
           SELECT count(*) FILTER (WHERE n > 1)::float / NULLIF(count(*), 0) AS share FROM z"""
    ).fetchone()["share"]
    if lag is not None:
        results.append(
            Result(
                "silver",
                "zone_price_consistency",
                "warn",
                lag <= 0.01,
                "zoned retailers",
                round(lag, 4),
                "<= 1%",
                "stores of one price zone should quote one price",
            )
        )

    items = conn.execute(
        """SELECT count(*) AS n, count(*) FILTER (WHERE category = 'Uncategorized') AS uncat,
                  count(*) FILTER (WHERE unit_type IS NOT NULL) AS parsed FROM silver.store_item"""
    ).fetchone()
    if items["n"]:
        results.append(
            Result(
                "silver",
                "categories_mapped",
                "warn",
                items["uncat"] == 0,
                "all",
                float(items["uncat"]),
                "0 items",
            )
        )
        results.append(
            Result(
                "silver",
                "unit_parse_coverage",
                "warn",
                items["parsed"] / items["n"] >= 0.95,
                "all",
                round(items["parsed"] / items["n"], 4),
                ">= 95%",
            )
        )

    m = conn.execute(
        """SELECT (SELECT count(*) FROM silver.match_review WHERE status = 'pending') AS pending,
                  (SELECT count(*) FROM silver.product WHERE quarantined) AS quarantined,
                  (SELECT count(*) FROM silver.product) AS products"""
    ).fetchone()
    results.append(
        Result(
            "silver",
            "review_backlog",
            "warn",
            m["pending"] <= 100,
            "matching",
            float(m["pending"]),
            "<= 100 pending",
        )
    )
    if m["products"]:
        share = m["quarantined"] / m["products"]
        results.append(
            Result(
                "silver",
                "quarantine_rate",
                "warn",
                share <= 0.05,
                "matching",
                round(share, 4),
                "<= 5% of products",
            )
        )

    jump = conn.execute(
        """WITH d AS (
               SELECT store_item_id, observed_date, price_qepik,
                      lag(price_qepik) OVER (PARTITION BY store_item_id, COALESCE(store_id, 0) ORDER BY observed_date) AS prev
               FROM silver.price_observation
               WHERE observed_date > (SELECT max(observed_date) FROM silver.price_observation) - 2)
           SELECT count(*) FILTER (WHERE prev IS NOT NULL AND (price_qepik > prev * 2.5 OR price_qepik < prev * 0.4))::float
                  / NULLIF(count(*) FILTER (WHERE prev IS NOT NULL), 0) AS share FROM d"""
    ).fetchone()["share"]
    if jump is not None:
        results.append(
            Result(
                "silver",
                "implausible_price_jumps",
                "warn",
                jump <= 0.005,
                "all",
                round(jump, 5),
                "<= 0.5% of items",
                "day-over-day moves beyond x2.5 / x0.4",
            )
        )

    for r in results:
        ops.record_dq(
            conn,
            run_id=run_id,
            layer=r.layer,
            check=r.check,
            severity=r.severity,
            passed=r.passed,
            scope=r.scope,
            observed=r.observed,
            threshold=r.threshold,
            detail=r.detail,
        )
        if not r.passed:
            log.warning(
                "data quality check failed",
                extra={
                    "check": r.check,
                    "scope": r.scope,
                    "severity": r.severity,
                    "observed": r.observed,
                    "threshold": r.threshold,
                },
            )
    return results
