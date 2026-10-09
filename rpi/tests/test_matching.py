"""Product matching: hard rules, scoring on ground truth, review queue and quarantine."""

from __future__ import annotations

import csv

import pytest

from rpi.flows import steps
from rpi.matching.evaluate import evaluate
from rpi.matching.matcher import (
    Canon,
    Item,
    Matcher,
    approve_review,
    display_name,
    reject_review,
    score_pair,
)
from rpi.tests.conftest import connect, generate_feeds


def _item(
    name, retailer="a", brand=None, unit=("ml", 1000.0), pack=None, category="Dairy & Eggs"
) -> Item:
    return Item(1, retailer, name, brand, unit[0], unit[1], pack, None, "none", category)


def _canon(item: Item) -> Canon:
    return Canon(
        1,
        item.category,
        item.brand,
        item.unit_type,
        item.unit_value,
        item.pack,
        item.tokens,
        item.fat,
        item.variants,
        {item.retailer},
    )


# ------------------------------------------------------------------------------ hard rules


def test_different_pack_size_is_never_the_same_product():
    a = _item("Xəzər Süd 3.2% 1 l", brand="xazar")
    b = _item("Xəzər Süd 3.2% 500 ml", brand="xazar", unit=("ml", 500.0))
    assert score_pair(b, _canon(a)) is None


def test_fat_percentage_separates_products():
    a = _item("Xəzər Süd 3.2% 1 l", brand="xazar")
    b = _item("Xəzər Süd 1.5% 1 l", brand="xazar")
    assert score_pair(b, _canon(a)) is None


def test_numeric_variants_are_kept_apart():
    a = _item("Duramax Batareya 2025", brand="duramax", unit=("pcs", 1.0))
    b = _item("Duramax Batareya 2032", brand="duramax", unit=("pcs", 1.0))
    assert score_pair(b, _canon(a)) is None


def test_different_brand_is_never_the_same_product():
    a = _item("Qafqaz Süd 3.2% 1 l", brand="qafqaz")
    b = _item("Nərgiz Süd 3.2% 1 l", brand="nargiz")
    assert score_pair(b, _canon(a)) is None


def test_small_typos_still_score_high():
    a = _item("Qafqaz Qatıq 500 q", brand="qafqaz", unit=("g", 500.0))
    b = _item("Qafqaz Qatq 500 q", brand="qafqaz", unit=("g", 500.0))
    s = score_pair(b, _canon(a))
    assert s and s[0] >= 0.88


def test_weighed_goods_never_match_packaged_goods():
    weighed = _item("Pomidor kq", unit=("kg_bulk", None))
    packaged = _item("Pomidor 1 kq", unit=("g", 1000.0))
    assert score_pair(packaged, _canon(weighed)) is None


def test_display_name_survives_turkic_capitals():
    assert display_name("XƏZƏR SÜD 3.2% 1L") == "Xəzər Süd 3.2% 1l"
    assert display_name("MİLLA ŞOKOLAD") == "Milla Şokolad"


# ------------------------------------------------------------------------------ scored on ground truth

pytestmark = pytest.mark.slow


def test_matching_accuracy_against_ground_truth(db, built_db):
    """Thresholds are deliberately below what was measured (precision 1.0, recall ~0.97) to allow for drift."""
    ev = evaluate(db, built_db["truth"])
    assert ev["items_evaluated"] == built_db["manifest"]["items"]
    pub = ev["published_pairs"]
    assert pub["precision"] >= 0.98, ev
    assert pub["recall"] >= 0.90, ev
    assert ev["by_method"].get("ean", 0) > 0 and ev["by_method"].get("fingerprint", 0) > 0


def test_injected_barcode_collisions_are_quarantined(db, built_db):
    """Two different products sharing one barcode must not be published as one."""
    with open(built_db["truth"] / "items.csv", encoding="utf-8") as f:
        wrong = [(r["retailer_code"], r["sku"]) for r in csv.DictReader(f) if r["wrong_ean"] == "1"]
    assert len(wrong) == built_db["manifest"]["injected"]["ean_collision"] > 0
    flagged = 0
    for retailer, sku in wrong:
        row = db.execute(
            """SELECT p.quarantined, p.quarantine_reason FROM silver.store_item i
               JOIN silver.product_match m ON m.store_item_id = i.id JOIN silver.product p ON p.id = m.product_id
               WHERE i.retailer_code = %s AND i.sku = %s""",
            (retailer, sku),
        ).fetchone()
        flagged += bool(row["quarantined"])
    assert flagged == len(wrong)


def test_a_product_never_holds_two_skus_of_one_retailer_through_name_evidence(db):
    rows = db.execute(
        """SELECT m.product_id, i.retailer_code, count(*) AS n, count(DISTINCT i.ean) AS eans
           FROM silver.product_match m JOIN silver.store_item i ON i.id = m.store_item_id
           GROUP BY 1, 2 HAVING count(*) > 1"""
    ).fetchall()
    # Allowed only when joined by a shared barcode (EAN evidence), never by names.
    assert all(r["eans"] == 1 for r in rows)


def test_weighed_and_packaged_goods_are_never_in_one_product(db):
    bad = db.execute(
        """SELECT m.product_id FROM silver.product_match m JOIN silver.store_item i ON i.id = m.store_item_id
           GROUP BY 1 HAVING count(DISTINCT (i.unit_type = 'kg_bulk')) > 1"""
    ).fetchall()
    assert bad == []


def test_uncertain_matches_wait_in_the_queue_with_a_score(db):
    q = db.execute("SELECT * FROM silver.match_review WHERE status = 'pending'").fetchall()
    assert q, "the synthetic data contains brand-less and typo'd names that must land in review"
    assert all(0.70 <= float(r["score"]) < 1.0 for r in q)
    assert all("name_similarity" in r["features"] for r in q)
    # An item awaiting review is its own product until a human decides: nothing is merged on a guess.
    pending_items = db.execute(
        """SELECT count(*) AS n FROM silver.product_match m WHERE m.status = 'pending_review'
           AND (SELECT count(*) FROM silver.product_match x WHERE x.product_id = m.product_id) > 1"""
    ).fetchone()
    assert pending_items["n"] == 0


# ------------------------------------------------------------------------------ stateful: private database


def _built(fresh_db):
    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=4, products=80)
    steps.ingest_step()
    steps.silver_step()
    return steps.match_step()


def test_matching_is_idempotent(fresh_db):
    first = _built(fresh_db)
    with connect(fresh_db["url"]) as c:
        before = c.execute("SELECT count(*) AS n FROM silver.product").fetchone()["n"]
    second = steps.match_step()
    with connect(fresh_db["url"]) as c:
        after = c.execute("SELECT count(*) AS n FROM silver.product").fetchone()["n"]
    assert first["new"] > 0
    assert (second["new"], second["review"], second["ean"], second["fuzzy_auto"]) == (0, 0, 0, 0)
    assert before == after


def test_approving_a_review_merges_the_item_and_removes_the_orphan_product(fresh_db):
    _built(fresh_db)
    with connect(fresh_db["url"]) as c:
        r = c.execute(
            "SELECT * FROM silver.match_review WHERE status = 'pending' ORDER BY score DESC LIMIT 1"
        ).fetchone()
        old = c.execute(
            "SELECT product_id FROM silver.product_match WHERE store_item_id = %s",
            (r["store_item_id"],),
        ).fetchone()["product_id"]
        approve_review(c, r["id"], "tester")
        c.commit()
        m = c.execute(
            "SELECT * FROM silver.product_match WHERE store_item_id = %s", (r["store_item_id"],)
        ).fetchone()
        orphan = c.execute(
            "SELECT count(*) AS n FROM silver.product WHERE id = %s", (old,)
        ).fetchone()["n"]
        decided = c.execute(
            "SELECT status, decided_by FROM silver.match_review WHERE id = %s", (r["id"],)
        ).fetchone()
    assert (
        m["product_id"] == r["candidate_product_id"]
        and m["status"] == "approved"
        and m["method"] == "review"
    )
    assert orphan == 0
    assert decided == {"status": "approved", "decided_by": "tester"}


def test_rejecting_all_candidates_makes_the_item_its_own_product_and_it_is_not_requeued(fresh_db):
    _built(fresh_db)
    with connect(fresh_db["url"]) as c:
        r = c.execute(
            "SELECT * FROM silver.match_review WHERE status = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()
        for rv in c.execute(
            "SELECT id FROM silver.match_review WHERE store_item_id = %s AND status = 'pending'",
            (r["store_item_id"],),
        ).fetchall():
            reject_review(c, rv["id"], "tester")
        c.commit()
        status = c.execute(
            "SELECT status FROM silver.product_match WHERE store_item_id = %s",
            (r["store_item_id"],),
        ).fetchone()["status"]
    assert status == "singleton"
    steps.match_step()
    with connect(fresh_db["url"]) as c:
        again = c.execute(
            "SELECT count(*) AS n FROM silver.match_review WHERE store_item_id = %s AND status = 'pending'",
            (r["store_item_id"],),
        ).fetchone()["n"]
    assert again == 0


def test_deciding_twice_is_an_error(fresh_db):
    _built(fresh_db)
    with connect(fresh_db["url"]) as c:
        rid = c.execute(
            "SELECT id FROM silver.match_review WHERE status = 'pending' LIMIT 1"
        ).fetchone()["id"]
        approve_review(c, rid)
        with pytest.raises(LookupError):
            approve_review(c, rid)


def test_matcher_uses_only_unmatched_items(fresh_db):
    _built(fresh_db)
    with connect(fresh_db["url"]) as c:
        n = c.execute("SELECT count(*) AS n FROM silver.store_item").fetchone()["n"]
        m = Matcher(c)
        m.run()
        assert m.stats.total() == 0 and n > 0
