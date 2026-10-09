"""Silver layer: validation, deduplication, money handling, append-only history, batch gates."""

from __future__ import annotations

import pytest
from psycopg import errors

from rpi.flows import steps
from rpi.tests.conftest import connect, generate_feeds, use_database

pytestmark = pytest.mark.slow


def _rejected_by_reason(db) -> dict[str, int]:
    return {
        r["reason"]: r["n"]
        for r in db.execute("SELECT reason, count(*) AS n FROM silver.rejected_record GROUP BY 1")
    }


def test_every_rejected_row_matches_an_injected_defect(db, built_db):
    """The generator records what it broke; silver must reject exactly that, no more and no less."""
    injected = built_db["manifest"]["injected"]
    got = _rejected_by_reason(db)
    assert got.get("nonpositive_price", 0) == injected["zero_price"] + injected["negative_price"]
    assert got.get("missing_name", 0) == injected["empty_name"]
    assert got.get("duplicate_row", 0) == injected["duplicate_row"]
    assert got.get("unparseable_line", 0) == injected["unparseable_line"]
    assert set(got) <= {"nonpositive_price", "missing_name", "duplicate_row", "unparseable_line"}


def test_no_row_disappears_silently(db):
    """rows in each bronze file == observations + rejected rows (the data-quality check relies on it)."""
    bad = db.execute(
        """SELECT b.batch_id, b.row_count,
                  (SELECT count(*) FROM silver.price_observation o WHERE o.batch_id = b.batch_id) AS obs,
                  (SELECT count(*) FROM silver.rejected_record x WHERE x.batch_id = b.batch_id) AS rej
           FROM bronze.ingest_batch b WHERE b.kind = 'prices' AND b.status = 'silver_done'"""
    ).fetchall()
    assert bad
    assert [r for r in bad if r["row_count"] != r["obs"] + r["rej"]] == []


def test_money_columns_are_integers_never_floats(db):
    cols = db.execute(
        """SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns
           WHERE column_name LIKE '%%qepik%%' AND table_schema IN ('silver', 'gold')"""
    ).fetchall()
    assert len(cols) >= 4
    assert {c["data_type"] for c in cols} <= {"integer", "bigint"}, cols


def test_float_prices_on_the_wire_become_exact_qepik(db):
    """absheron sends price_azn as a JSON float; check silver stored exactly round(x * 100)."""
    raw = db.execute(
        """SELECT r.payload->>'item_code' AS sku, r.payload->>'store_code' AS store, (r.payload->>'price_azn')::numeric AS azn, b.business_date
           FROM bronze.raw_record r JOIN bronze.ingest_batch b USING (batch_id)
           WHERE b.source = 'absheron' AND b.kind = 'prices' AND (r.payload->>'price_azn')::numeric > 0 LIMIT 300"""
    ).fetchall()
    assert raw
    for r in raw:
        got = db.execute(
            """SELECT o.price_qepik FROM silver.price_observation o JOIN silver.store_item i ON i.id = o.store_item_id
               JOIN silver.store s ON s.id = o.store_id
               WHERE i.sku = %s AND s.store_code = %s AND o.observed_date = %s""",
            (r["sku"], r["store"], r["business_date"]),
        ).fetchone()
        if got:  # a duplicate or conflicting twin may legitimately have been rejected
            assert got["price_qepik"] == int(r["azn"] * 100)


def test_history_is_append_only(db):
    with pytest.raises(errors.RaiseException, match="append-only"):
        db.execute(
            "UPDATE silver.price_observation SET price_qepik = price_qepik + 1 WHERE id = (SELECT min(id) FROM silver.price_observation)"
        )
    db.rollback()
    with pytest.raises(errors.RaiseException, match="append-only"):
        db.execute(
            "DELETE FROM silver.price_observation WHERE id = (SELECT min(id) FROM silver.price_observation)"
        )


def test_unique_index_catches_duplicates_even_when_store_is_null(db):
    """NULL <> NULL: a plain UNIQUE(store_item_id, store_id, date) would let this through."""
    row = db.execute(
        "SELECT store_item_id, observed_date, observed_at, price_qepik, batch_id FROM silver.price_observation WHERE store_id IS NULL LIMIT 1"
    ).fetchone()
    with pytest.raises(errors.UniqueViolation):
        db.execute(
            "INSERT INTO silver.price_observation (store_item_id, store_id, observed_date, observed_at, price_qepik, batch_id) "
            "VALUES (%s, NULL, %s, %s, %s, %s)",
            (
                row["store_item_id"],
                row["observed_date"],
                row["observed_at"],
                row["price_qepik"],
                row["batch_id"],
            ),
        )


def test_zoned_retailer_rows_carry_a_store_and_single_price_rows_do_not(db):
    zoned = db.execute(
        """SELECT count(*) FILTER (WHERE o.store_id IS NULL) AS nulls, count(*) AS n FROM silver.price_observation o
           JOIN silver.store_item i ON i.id = o.store_item_id WHERE i.retailer_code = 'absheron'"""
    ).fetchone()
    single = db.execute(
        """SELECT count(*) FILTER (WHERE o.store_id IS NOT NULL) AS stores FROM silver.price_observation o
           JOIN silver.store_item i ON i.id = o.store_item_id WHERE i.retailer_code <> 'absheron'"""
    ).fetchone()
    assert zoned["n"] > 0 and zoned["nulls"] == 0
    assert single["stores"] == 0


def test_items_are_enriched_from_messy_names(db):
    rows = db.execute("SELECT * FROM silver.store_item").fetchall()
    assert rows
    assert all(r["category"] != "Uncategorized" for r in rows)
    assert all(r["name_norm"] == r["name_norm"].lower() and "̇" not in r["name_norm"] for r in rows)
    weighed = [r for r in rows if r["unit_type"] == "kg_bulk"]
    assert weighed and all(r["unit_value"] is None for r in weighed)
    assert {r["ean_kind"] for r in rows} == {"global", "internal", "none"}
    assert all(r["ean"] is None or r["ean"].isdigit() for r in rows)


def test_reprocessing_changes_nothing(built_db):
    with use_database(built_db["url"], built_db["landing"], built_db["truth"]):
        with connect(built_db["url"]) as c:
            before = c.execute(
                "SELECT (SELECT count(*) FROM silver.price_observation) AS o, (SELECT count(*) FROM bronze.raw_record) AS r"
            ).fetchone()
        ing = steps.ingest_step()
        sil = steps.silver_step()
        with connect(built_db["url"]) as c:
            after = c.execute(
                "SELECT (SELECT count(*) FROM silver.price_observation) AS o, (SELECT count(*) FROM bronze.raw_record) AS r"
            ).fetchone()
    assert ing["files_ingested"] == 0 and sil["batches"] == 0
    assert before == after


# ------------------------------------------------------------------------------ batch gates


def _run_with_incident(fresh_db, incident: str) -> tuple[dict, list]:
    """Seven healthy days, then one incident day for caspianmart."""
    generate_feeds(
        fresh_db["landing"],
        fresh_db["truth"],
        days=8,
        products=60,
        clean=True,
        incidents=[incident],
    )
    steps.ingest_step()
    result = steps.silver_step("incident-run")
    with connect(fresh_db["url"]) as c:
        alerts = c.execute("SELECT * FROM ops.alert WHERE source = 'caspianmart'").fetchall()
    return result, alerts


def test_a_feed_in_the_wrong_unit_is_rejected_as_a_whole(fresh_db):
    """A x100 price feed (qepik read as AZN) has a median far outside the Baku range."""
    result, alerts = _run_with_incident(fresh_db, "unit_mismatch:caspianmart:2026-09-30")
    assert (
        result["batches_rejected"] == 1
        and result["rejected_batches"][0]["reason"] == "median_price_out_of_range"
    )
    assert alerts and "median_price_out_of_range" in alerts[0]["title"]
    with connect(fresh_db["url"]) as c:
        n = c.execute(
            """SELECT count(*) AS n FROM silver.price_observation o JOIN silver.store_item i ON i.id = o.store_item_id
               WHERE i.retailer_code = 'caspianmart' AND o.observed_date = '2026-09-30'"""
        ).fetchone()["n"]
        status = c.execute(
            "SELECT status, status_detail FROM bronze.ingest_batch WHERE source = 'caspianmart' AND business_date = '2026-09-30'"
        ).fetchone()
    assert n == 0, "a rejected batch must not leak a single observation into silver"
    assert status["status"] == "rejected" and "outside" in status["status_detail"]


def test_a_truncated_feed_is_rejected_instead_of_looking_like_an_assortment_collapse(fresh_db):
    result, alerts = _run_with_incident(fresh_db, "truncated:caspianmart:2026-09-30")
    assert result["rejected_batches"] == [{"source": "caspianmart", "reason": "row_count_collapse"}]
    assert alerts


def test_unknown_source_is_rejected_but_kept_in_bronze(fresh_db):
    f = fresh_db["landing"] / "mystery" / "mystery_2026-09-30.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text('{"sku": "1", "title": "x", "price": "1.00"}\n')
    steps.ingest_step()
    res = steps.silver_step()
    assert res["rejected_batches"] == [{"source": "mystery", "reason": "unknown_source"}]
    with connect(fresh_db["url"]) as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM bronze.raw_record WHERE source = 'mystery'"
            ).fetchone()["n"]
            == 1
        )


def test_unknown_store_rows_are_rejected_individually(fresh_db):
    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=2, products=40, clean=True)
    # Hide the stores file so every absheron row refers to an unknown store.
    for p in (fresh_db["landing"] / "absheron").glob("*stores*"):
        p.unlink()
    steps.ingest_step()
    res = steps.silver_step()
    assert res["batches_rejected"] >= 1  # >20% of the zoned feed is invalid -> whole batch refused
    assert any(b["source"] == "absheron" for b in res["rejected_batches"])
