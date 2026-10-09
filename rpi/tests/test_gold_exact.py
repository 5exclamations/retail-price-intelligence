"""Exact-value tests for the dbt marts on a tiny hand-built dataset.

Statistical tests on noisy synthetic data can only check tendencies. Here every price is chosen by hand,
so each mart can be checked to the qepik and to the basis point.

Retailers R1..R4 sell products P1..P4 (all Dairy & Eggs) on two full ISO weeks. Week 1 prices: R1 9.00,
R2 10.00, R3 10.00, R4 11.00 AZN; week 2 everything is 10% dearer. On the last day R3 runs a promotion on
P1 (8.00, shelf 'was' 16.00: inflated) and R4 one on P2 (10.00, 'was' 12.10: its own regular price).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from rpi.tests.conftest import connect
from rpi.warehouse import dbt

pytestmark = pytest.mark.slow

PRICE_W1 = {"r1": 900, "r2": 1000, "r3": 1000, "r4": 1100}
LAST = date(2026, 9, 27)
FIRST = date(2026, 9, 14)  # a Monday


@pytest.fixture()
def tiny(fresh_db):
    with connect(fresh_db["url"]) as c:
        for code in PRICE_W1:
            c.execute(
                "INSERT INTO silver.retailer (code, name, price_model) VALUES (%s, %s, 'single')",
                (code, code.upper()),
            )
        batch = c.execute(
            "INSERT INTO bronze.ingest_batch (source, kind, landing_path, file_sha256, file_bytes, business_date, row_count, status) "
            "VALUES ('r1', 'prices', 'x', 'x', 1, %s, 1, 'silver_done') RETURNING batch_id",
            (LAST,),
        ).fetchone()["batch_id"]
        pids = [
            c.execute(
                "INSERT INTO silver.product (name, category, unit_type, unit_value) VALUES (%s, 'Dairy & Eggs', 'ml', 1000) RETURNING id",
                (f"Product {i}",),
            ).fetchone()["id"]
            for i in range(1, 5)
        ]
        for p_idx, pid in enumerate(pids, start=1):
            for code, base in PRICE_W1.items():
                item = c.execute(
                    """INSERT INTO silver.store_item (retailer_code, sku, name_raw, name_norm, unit_type, unit_value, category, first_seen, last_seen)
                       VALUES (%s, %s, %s, %s, 'ml', 1000, 'Dairy & Eggs', %s, %s) RETURNING id""",
                    (code, f"{code}-{p_idx}", f"P{p_idx}", f"p{p_idx}", FIRST, LAST),
                ).fetchone()["id"]
                c.execute(
                    "INSERT INTO silver.product_match (store_item_id, product_id, method, confidence, status) VALUES (%s, %s, 'ean', 1, 'auto')",
                    (item, pid),
                )
                for day in range(14):
                    d = FIRST + timedelta(days=day)
                    price = base if day < 7 else base * 11 // 10
                    old = None
                    if d == LAST and code == "r3" and p_idx == 1:
                        price, old = 800, 1600
                    if d == LAST and code == "r4" and p_idx == 2:
                        price, old = 1000, 1210
                    c.execute(
                        """INSERT INTO silver.price_observation (store_item_id, observed_date, observed_at, price_qepik, old_price_qepik, batch_id)
                           VALUES (%s, %s, %s::timestamptz, %s, %s, %s)""",
                        (item, d, f"{d} 06:00+04", price, old, batch),
                    )
        c.commit()
    summary = dbt("build")
    return {"url": fresh_db["url"], "pids": pids, "dbt": summary}


def test_price_index_is_exactly_the_geometric_mean_of_price_relatives(tiny):
    with connect(tiny["url"]) as c:
        rows = {
            (str(r["week_start"]), r["category"]): r
            for r in c.execute("SELECT * FROM gold.mart_price_index")
        }
    assert float(rows[("2026-09-14", "All categories")]["index_regular"]) == 100.0
    assert float(rows[("2026-09-21", "All categories")]["index_regular"]) == 110.0
    assert float(rows[("2026-09-21", "Dairy & Eggs")]["wow_change_pct"]) == 10.0
    assert rows[("2026-09-21", "All categories")]["n_items"] == 16  # 4 products x 4 retailers


def test_competitiveness_is_measured_against_the_market_median(tiny):
    with connect(tiny["url"]) as c:
        rows = {
            r["price_point_key"]: r
            for r in c.execute(
                "SELECT * FROM gold.mart_retailer_competitiveness WHERE week_start = '2026-09-14'"
            )
        }
    assert {k: float(v["price_index_vs_market"]) for k, v in rows.items()} == {
        "r1:all": 90.0,
        "r2:all": 100.0,
        "r3:all": 100.0,
        "r4:all": 110.0,
    }
    assert float(rows["r1:all"]["share_cheapest_pct"]) == 100.0
    assert float(rows["r4:all"]["share_cheapest_pct"]) == 0.0
    assert rows["r1:all"]["n_products"] == 4


def test_promotions_are_judged_against_the_market_not_the_shelf_tag(tiny):
    with connect(tiny["url"]) as c:
        rows = {
            r["price_point_key"]: r for r in c.execute("SELECT * FROM gold.mart_promo_analysis")
        }
    inflated, honest = rows["r3:all"], rows["r4:all"]
    # R3: shelf says 50% off (1600 -> 800); the other chains charge 990/1100/1210, median 1100 -> real 27.27%.
    assert (
        inflated["claimed_discount_bp"],
        inflated["market_ref_qepik"],
        inflated["real_discount_bp"],
    ) == (5000, 1100, 2727)
    assert inflated["inflated_flag"] and not inflated["fake_flag"] and inflated["ref_reliable"]
    # The shelf 'was' 16.00 was never a price R3 charged: its last ordinary price was 11.00.
    assert inflated["regular_price_qepik"] == 1100 and inflated["claim_vs_own_regular_bp"] == 4545
    # R4: 'was' 12.10 is its real regular price, 17.36% off by its tag, 9.09% against the market. Honest.
    assert (
        honest["claimed_discount_bp"],
        honest["market_ref_qepik"],
        honest["real_discount_bp"],
    ) == (1736, 1100, 909)
    assert not honest["inflated_flag"] and honest["claim_vs_own_regular_bp"] == 0


def test_basket_totals_are_integer_sums_and_rank_only_complete_stores(tiny):
    with connect(tiny["url"]) as c:
        rows = {
            r["price_point_key"]: r
            for r in c.execute(
                "SELECT * FROM gold.mart_cheapest_basket WHERE observed_date = %s", (LAST,)
            )
        }
        items = c.execute("SELECT * FROM gold.basket_definition ORDER BY product_key").fetchall()
    assert [i["quantity"] for i in items] == [2, 2] and len(
        items
    ) == 2  # best-covered two products, 2 units each
    assert {k: v["total_qepik"] for k, v in rows.items()} == {
        "r1:all": 3960,
        "r2:all": 4400,
        "r3:all": 3800,
        "r4:all": 4420,
    }
    assert {k: v["rank_overall"] for k, v in rows.items()} == {
        "r3:all": 1,
        "r1:all": 2,
        "r2:all": 3,
        "r4:all": 4,
    }
    assert all(r["is_complete"] for r in rows.values())


def test_current_price_view_has_the_latest_price_and_its_timestamp(tiny):
    with connect(tiny["url"]) as c:
        r = c.execute(
            "SELECT * FROM gold.mart_price_current WHERE product_key = %s AND price_point_key = 'r3:all'",
            (tiny["pids"][0],),
        ).fetchone()
    assert (r["price_qepik"], r["old_price_qepik"], r["is_promo"], r["days_since_observed"]) == (
        800,
        1600,
        True,
        0,
    )
    assert (
        r["observed_at"].isoformat() == "2026-09-27T02:00:00+00:00"
    )  # 06:00 Baku time, stored in UTC
