"""Gold layer (dbt): the build, the model invariants and incremental behaviour."""

from __future__ import annotations

import pytest

from rpi.flows import steps
from rpi.tests.conftest import connect, generate_feeds, use_database
from rpi.warehouse import dbt

pytestmark = pytest.mark.slow


def test_dbt_build_passes_all_models_and_tests(built_db):
    s = built_db["dbt"]
    assert s.tests_failed == 0 and not s.failures
    assert s.tests_passed >= 55 and s.models >= 25


def test_fact_grain_is_item_price_point_day(db):
    dup = db.execute(
        "SELECT count(*) AS n FROM (SELECT 1 FROM gold.fct_price_daily GROUP BY store_item_id, price_point_key, observed_date HAVING count(*) > 1) d"
    ).fetchone()["n"]
    assert dup == 0
    # one row per item/zone/day: absheron's two stores per zone collapse into one price
    silver_rows = db.execute(
        """SELECT count(*) AS n FROM (SELECT 1 FROM silver.price_observation o
           LEFT JOIN silver.store s ON s.id = o.store_id GROUP BY o.store_item_id, COALESCE(s.price_zone, 'all'), o.observed_date) x"""
    ).fetchone()["n"]
    assert (
        db.execute("SELECT count(*) AS n FROM gold.fct_price_daily").fetchone()["n"] == silver_rows
    )


def test_zoned_chain_is_always_split_by_zone(db):
    rows = db.execute("SELECT retailer_code, price_zone FROM gold.dim_price_point").fetchall()
    zoned = {r["price_zone"] for r in rows if r["retailer_code"] == "absheron"}
    assert zoned == {"A", "B", "C", "D"}
    assert all(r["price_zone"] == "all" for r in rows if r["retailer_code"] != "absheron")


def test_quarantined_products_never_reach_gold(db):
    q = {r["id"] for r in db.execute("SELECT id FROM silver.product WHERE quarantined")}
    assert q
    for table, col in (
        ("gold.dim_product", "product_key"),
        ("gold.dim_store_item", "product_key"),
        ("gold.mart_price_current", "product_key"),
        ("gold.mart_promo_analysis", "product_key"),
    ):
        leaked = db.execute(
            f"SELECT count(*) AS n FROM {table} WHERE {col} = ANY(%s)", (list(q),)
        ).fetchone()["n"]
        assert leaked == 0, table


def test_regular_price_is_the_last_ordinary_price_not_the_shelf_claim(db):
    """On every promo day the regular price is the item's last non-promo price, whatever 'was' says."""
    mismatches = db.execute(
        """SELECT count(*) AS n FROM gold.fct_price_daily f
           WHERE f.is_promo AND f.regular_price_source = 'observed'
             AND f.regular_price_qepik IS DISTINCT FROM (
                 SELECT p.price_qepik FROM gold.fct_price_daily p
                 WHERE p.store_item_id = f.store_item_id AND p.price_point_key = f.price_point_key
                   AND p.observed_date < f.observed_date AND NOT p.is_promo
                 ORDER BY p.observed_date DESC LIMIT 1)"""
    ).fetchone()["n"]
    assert mismatches == 0
    inflated = db.execute(
        "SELECT count(*) AS n FROM gold.fct_price_daily WHERE is_promo AND regular_price_source = 'observed' AND old_price_qepik > regular_price_qepik * 1.3"
    ).fetchone()["n"]
    assert inflated > 0, "the data contains inflated 'was' prices; regular price must ignore them"


def test_inflated_promotions_are_flagged_and_honest_ones_are_not(db):
    r = db.execute(
        """SELECT count(*) FILTER (WHERE inflated_flag) AS inflated, count(*) FILTER (WHERE ref_reliable) AS reliable,
                  count(*) FILTER (WHERE inflated_flag AND NOT ref_reliable) AS impossible,
                  count(*) FILTER (WHERE inflated_flag AND claimed_discount_bp - real_discount_bp <= 1500) AS wrong
           FROM gold.mart_promo_analysis"""
    ).fetchone()
    share = r["inflated"] / r["reliable"]
    assert r["impossible"] == 0 and r["wrong"] == 0
    assert 0.01 <= share <= 0.15, (
        f"~5% of promotions were generated with an inflated 'was' price, got {share:.1%}"
    )


def test_discounts_are_integer_basis_points(db):
    types = db.execute(
        """SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'gold'
           AND table_name = 'mart_promo_analysis' AND column_name LIKE '%%_bp'"""
    ).fetchall()
    assert types and {t["data_type"] for t in types} == {"integer"}


def test_price_index_starts_at_100_and_follows_the_drift_in_the_data(db):
    rows = db.execute(
        "SELECT * FROM gold.mart_price_index WHERE category = 'All categories' ORDER BY week_start"
    ).fetchall()
    assert float(rows[0]["index_regular"]) == 100.0
    assert len(rows) >= 2
    # The generator inflates every category by 0.3-1.2% a month, so the index must rise slowly, never jump.
    assert all(abs(float(r["wow_change_pct"])) < 3 for r in rows[1:])
    # The effective index moves with promotion intensity (relative to the base week) but stays near the regular one.
    assert all(abs(float(r["index_effective"]) - float(r["index_regular"])) < 5 for r in rows)


def test_default_basket_only_ranks_complete_price_points(db):
    rows = db.execute(
        "SELECT * FROM gold.mart_cheapest_basket WHERE observed_date = (SELECT max(observed_date) FROM gold.mart_cheapest_basket)"
    ).fetchall()
    assert rows
    assert all((r["rank_overall"] is not None) == r["is_complete"] for r in rows)
    assert all(isinstance(r["total_qepik"], int) for r in rows)


def test_current_prices_carry_their_observation_time(db):
    assert (
        db.execute(
            "SELECT count(*) AS n FROM gold.mart_price_current WHERE observed_at IS NULL"
        ).fetchone()["n"]
        == 0
    )


def test_dbt_snapshots_keep_match_history(db):
    assert (
        db.execute(
            "SELECT count(*) AS n FROM gold.snap_product_match WHERE dbt_valid_to IS NULL"
        ).fetchone()["n"]
        > 0
    )
    assert db.execute("SELECT count(*) AS n FROM gold.snap_store_zone").fetchone()["n"] == 8


# ------------------------------------------------------------------------------ incremental behaviour


def test_incremental_run_adds_only_new_days_and_leaves_history_alone(fresh_db):
    generate_feeds(
        fresh_db["landing"],
        fresh_db["truth"],
        days=12,
        products=70,
        end=__import__("datetime").date(2026, 9, 28),
        to_date=__import__("datetime").date(2026, 9, 25),
    )
    steps.ingest_step()
    steps.silver_step()
    steps.match_step()  # noqa: E702
    dbt("build")
    with connect(fresh_db["url"]) as c:
        before = c.execute(
            "SELECT count(*) AS n, max(observed_date) AS last, md5(string_agg(price_qepik::text || regular_price_qepik::text, ',' ORDER BY store_item_id, price_point_key, observed_date)) AS h "
            "FROM gold.fct_price_daily WHERE observed_date <= '2026-09-17'"
        ).fetchone()
        total_before = c.execute("SELECT count(*) AS n FROM gold.fct_price_daily").fetchone()["n"]
    # tomorrow's files arrive
    generate_feeds(
        fresh_db["landing"],
        fresh_db["truth"],
        days=12,
        products=70,
        end=__import__("datetime").date(2026, 9, 28),
        from_date=__import__("datetime").date(2026, 9, 26),
    )
    steps.ingest_step()
    steps.silver_step()
    steps.match_step()  # noqa: E702
    dbt("build")
    with connect(fresh_db["url"]) as c:
        after = c.execute(
            "SELECT count(*) AS n, max(observed_date) AS last, md5(string_agg(price_qepik::text || regular_price_qepik::text, ',' ORDER BY store_item_id, price_point_key, observed_date)) AS h "
            "FROM gold.fct_price_daily WHERE observed_date <= '2026-09-17'"
        ).fetchone()
        total_after = c.execute(
            "SELECT count(*) AS n, max(observed_date) AS last FROM gold.fct_price_daily"
        ).fetchone()
    assert before == after, "history before the incremental window must be untouched"
    assert total_after["n"] > total_before and str(total_after["last"]) == "2026-09-28"
    dbt("build")  # a third run with no new data changes nothing
    with connect(fresh_db["url"]) as c:
        assert (
            c.execute("SELECT count(*) AS n FROM gold.fct_price_daily").fetchone()["n"]
            == total_after["n"]
        )


def test_a_review_decision_reaches_the_whole_history_after_the_next_build(fresh_db):
    """Facts are keyed by SKU, so re-pointing a SKU to another product re-labels all its history."""
    from rpi.matching.matcher import approve_review

    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=6, products=80)
    steps.ingest_step()
    steps.silver_step()
    steps.match_step()  # noqa: E702
    dbt("build")
    with connect(fresh_db["url"]) as c:
        r = c.execute(
            "SELECT * FROM silver.match_review WHERE status = 'pending' ORDER BY score DESC LIMIT 1"
        ).fetchone()
        days_of_item = c.execute(
            "SELECT count(*) AS n FROM gold.fct_price_daily WHERE store_item_id = %s",
            (r["store_item_id"],),
        ).fetchone()["n"]
        approve_review(c, r["id"], "tester")
        c.commit()
    dbt("build")
    with connect(fresh_db["url"]) as c:
        got = c.execute(
            "SELECT product_key FROM gold.dim_store_item WHERE store_item_id = %s",
            (r["store_item_id"],),
        ).fetchone()
        history = c.execute(
            """SELECT count(*) AS n FROM gold.fct_price_daily f JOIN gold.dim_store_item b USING (store_item_id)
               WHERE b.product_key = %s AND f.store_item_id = %s""",
            (r["candidate_product_id"], r["store_item_id"]),
        ).fetchone()["n"]
    assert got["product_key"] == r["candidate_product_id"]
    assert history == days_of_item > 0


def test_use_database_context_restores_environment(built_db):
    import os

    before = os.environ.get("RPI_DATABASE_URL")
    with use_database("postgresql://x/y"):
        assert os.environ["RPI_DATABASE_URL"] == "postgresql://x/y"
    assert os.environ.get("RPI_DATABASE_URL") == before
