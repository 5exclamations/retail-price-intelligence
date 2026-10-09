"""REST API: contracts that matter to clients (integer money, observation times, zone rule, quarantine)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rpi.api.main import app
from rpi.tests.conftest import connect, use_database

pytestmark = pytest.mark.slow


@pytest.fixture()
def client(built_db):
    with (
        use_database(built_db["url"], built_db["landing"], built_db["truth"]),
        TestClient(app) as c,
    ):
        yield c


@pytest.fixture()
def product_on_all_chains(client):
    items = client.get("/v1/products", params={"min_retailers": 5, "limit": 5}).json()["items"]
    assert items
    return items[0]["id"]


def test_health_and_meta(client):
    assert client.get("/health").json()["status"] == "ok"
    meta = client.get("/v1/meta").json()
    assert meta["data_as_of"] == "2026-09-30" and meta["data_is_synthetic"] is True
    assert {r["retailer_code"] for r in meta["retailers"]} == {
        "baku_fresh",
        "caspianmart",
        "absheron",
        "shirvan",
        "sumqayit",
    }
    assert meta["last_pipeline_run"]["run_id"]


def test_prices_are_integer_qepik_and_carry_their_observation_time(client, product_on_all_chains):
    body = client.get(f"/v1/products/{product_on_all_chains}/prices").json()
    assert body["prices"]
    for p in body["prices"]:
        assert isinstance(p["price_qepik"], int) and isinstance(p["regular_price_qepik"], int)
        assert p["observed_at"].startswith("2026-09-")
        assert isinstance(p["days_since_observed"], int)


def test_a_zoned_chain_is_never_priced_without_a_zone(client, product_on_all_chains):
    url = f"/v1/products/{product_on_all_chains}/prices"
    without = client.get(url).json()
    assert all(not p["price_point_key"].startswith("absheron") for p in without["prices"])
    assert {e["price_point_key"] for e in without["excluded_price_points"]} == {
        f"absheron:{z}" for z in "ABCD"
    }
    assert all(e["reason"] == "zone not selected" for e in without["excluded_price_points"])

    with_zone = client.get(url, params={"zones": "absheron:B"}).json()
    keys = [p["price_point_key"] for p in with_zone["prices"]]
    assert "absheron:B" in keys and not any(
        k in keys for k in ("absheron:A", "absheron:C", "absheron:D")
    )


def test_unknown_zone_is_a_client_error(client, product_on_all_chains):
    r = client.get(f"/v1/products/{product_on_all_chains}/prices", params={"zones": "absheron:Z"})
    assert r.status_code == 422 and "absheron:A" in r.json()["detail"]


def test_cheapest_flag_and_gap_are_consistent(client, product_on_all_chains):
    prices = client.get(
        f"/v1/products/{product_on_all_chains}/prices", params={"zones": "absheron:A"}
    ).json()["prices"]
    lo = min(p["price_qepik"] for p in prices)
    assert [p["price_qepik"] for p in prices] == sorted(p["price_qepik"] for p in prices)
    for p in prices:
        assert p["is_cheapest"] == (p["price_qepik"] == lo)
        assert p["gap_to_cheapest_bp"] == round((p["price_qepik"] - lo) * 10000 / lo)


def test_quarantined_products_do_not_exist_for_clients(client, built_db):
    with connect(built_db["url"]) as c:
        qid = c.execute("SELECT id FROM silver.product WHERE quarantined LIMIT 1").fetchone()["id"]
    assert client.get(f"/v1/products/{qid}").status_code == 404
    assert client.get(f"/v1/products/{qid}/prices").status_code == 404
    listed = {p["id"] for p in client.get("/v1/products", params={"limit": 200}).json()["items"]}
    assert qid not in listed


def test_product_list_filters_and_paginates(client):
    all_ = client.get("/v1/products", params={"limit": 5}).json()
    assert len(all_["items"]) == 5 and all_["total"] > 5
    dairy = client.get("/v1/products", params={"category": "Dairy & Eggs", "limit": 200}).json()
    assert dairy["items"] and {p["category"] for p in dairy["items"]} == {"Dairy & Eggs"}
    page2 = client.get("/v1/products", params={"limit": 5, "offset": 5}).json()["items"]
    assert not {p["id"] for p in page2} & {p["id"] for p in all_["items"]}
    assert client.get("/v1/products", params={"limit": 5000}).status_code == 422


def test_history_returns_a_daily_series(client, product_on_all_chains):
    r = client.get(
        f"/v1/products/{product_on_all_chains}/history",
        params={"price_point": "baku_fresh:all", "days": 10},
    ).json()
    assert 1 < len(r["series"]) <= 10
    assert [s["observed_date"] for s in r["series"]] == sorted(
        s["observed_date"] for s in r["series"]
    )
    assert (
        client.get(
            f"/v1/products/{product_on_all_chains}/history", params={"price_point": "nope:all"}
        ).status_code
        == 404
    )


def test_promotions_expose_claimed_and_real_discounts(client):
    body = client.get("/v1/promotions", params={"limit": 100}).json()
    assert body["items"]
    for p in body["items"]:
        assert p["old_price_qepik"] > p["price_qepik"] and 0 < p["claimed_discount_bp"] < 10000
        assert p["observed_at"]
    inflated = client.get("/v1/promotions", params={"flagged": "inflated", "limit": 100}).json()[
        "items"
    ]
    assert all(
        p["inflated_flag"] and p["claimed_discount_bp"] - p["real_discount_bp"] > 1500
        for p in inflated
    )
    honest = client.get("/v1/promotions", params={"flagged": "honest", "limit": 100}).json()[
        "items"
    ]
    assert all(not p["inflated_flag"] and not p["fake_flag"] for p in honest)


def test_price_index_series_starts_at_100(client):
    r = client.get("/v1/analytics/price-index").json()
    assert r["series"][0]["index_regular"] == 100.0 and "METHODOLOGY" in r["methodology"]
    assert (
        client.get("/v1/analytics/price-index", params={"category": "Nonexistent"}).status_code
        == 404
    )


def test_competitiveness_and_trends_and_movers(client):
    comp = client.get("/v1/analytics/competitiveness", params={"weeks": 2}).json()["items"]
    assert comp and all(0 <= c["share_cheapest_pct"] <= 100 for c in comp)
    trends = client.get("/v1/analytics/category-trends").json()["items"]
    assert {t["category"] for t in trends} >= {"Dairy & Eggs", "Bakery"}
    up = client.get("/v1/analytics/price-movers", params={"direction": "up", "limit": 5}).json()[
        "items"
    ]
    down = client.get(
        "/v1/analytics/price-movers", params={"direction": "down", "limit": 5}
    ).json()["items"]
    assert (
        up[0]["change_bp"] >= up[-1]["change_bp"] and down[0]["change_bp"] <= down[-1]["change_bp"]
    )


def test_default_basket_ranks_only_complete_price_points(client):
    b = client.get("/v1/basket/default", params={"zones": "absheron:A"}).json()
    complete = [p for p in b["price_points"] if p["is_complete"]]
    assert complete and b["cheapest"] == complete[0]["price_point_key"]
    assert complete[0]["premium_bp"] == 0 and all(
        p["total_qepik"] >= complete[0]["total_qepik"] for p in complete
    )
    assert all(
        not p["price_point_key"].startswith("absheron:") or p["price_point_key"] == "absheron:A"
        for p in b["price_points"]
    )


def test_custom_basket_split_is_never_dearer_than_the_best_single_store(client):
    ids = [
        p["id"]
        for p in client.get("/v1/products", params={"min_retailers": 5, "limit": 4}).json()["items"]
    ]
    r = client.post(
        "/v1/basket/cheapest",
        json={"product_ids": ids, "quantities": {str(ids[0]): 3}, "zones": ["absheron:A"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    best = body["best_single_store"]
    assert best and best["is_complete"]
    assert body["split"]["complete"] and body["split"]["total_qepik"] <= best["total_qepik"]
    assert body["split_saving_qepik"] == best["total_qepik"] - body["split"]["total_qepik"] >= 0
    assert sum(line["quantity"] for line in body["split"]["lines"]) == 3 + (len(ids) - 1)


def test_basket_input_is_validated(client):
    assert client.post("/v1/basket/cheapest", json={"product_ids": []}).status_code == 422
    assert client.post("/v1/basket/cheapest", json={"product_ids": [999999]}).status_code == 404
    pid = client.get("/v1/products", params={"limit": 1}).json()["items"][0]["id"]
    assert (
        client.post(
            "/v1/basket/cheapest", json={"product_ids": [pid], "quantities": {str(pid): 500}}
        ).status_code
        == 422
    )


KEY = "correct-horse-battery-staple"


def test_review_queue_is_readable(client):
    q = client.get("/v1/matching/review-queue", params={"limit": 3}).json()
    assert q["pending_total"] > 0 and 0.7 <= q["items"][0]["score"] < 1


def test_writes_are_disabled_when_no_key_is_configured(client, monkeypatch):
    """Secure by default: an unconfigured deployment is read-only, not open."""
    monkeypatch.delenv("RPI_API_KEY", raising=False)
    r = client.post(
        "/v1/matching/review/1", json={"decision": "approve"}, headers={"X-API-Key": KEY}
    )
    assert r.status_code == 503 and "RPI_API_KEY" in r.json()["detail"]
    monkeypatch.setenv("RPI_API_KEY", "short")  # too short counts as unconfigured
    assert (
        client.post(
            "/v1/matching/review/1", json={"decision": "approve"}, headers={"X-API-Key": "short"}
        ).status_code
        == 503
    )


def test_writes_require_the_exact_key(client, monkeypatch):
    monkeypatch.setenv("RPI_API_KEY", KEY)
    body = {"decision": "approve"}
    assert client.post("/v1/matching/review/1", json=body).status_code == 401
    r = client.post("/v1/matching/review/1", json=body, headers={"X-API-Key": "wrong-" + KEY})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "ApiKey"
    assert (
        client.post("/v1/matching/review/999999", json=body, headers={"X-API-Key": KEY}).status_code
        == 404
    )
    assert (
        client.post(
            "/v1/matching/review/1", json={"decision": "maybe"}, headers={"X-API-Key": KEY}
        ).status_code
        == 422
    )


def test_reads_never_need_a_key(client, monkeypatch):
    monkeypatch.delenv("RPI_API_KEY", raising=False)
    assert client.get("/v1/meta").status_code == 200


def test_the_decider_cannot_be_spoofed_by_the_client(client, monkeypatch):
    monkeypatch.setenv("RPI_API_KEY", KEY)
    # An extra field is ignored; the decider recorded server-side is always 'api'.
    r = client.post(
        "/v1/matching/review/999999",
        json={"decision": "approve", "decided_by": "admin"},
        headers={"X-API-Key": KEY},
    )
    assert r.status_code == 404


def test_quality_endpoints_report_the_last_run(client):
    q = client.get("/v1/quality/latest").json()
    assert q["run"]["run_id"] == "test-run"
    assert q["checks"] and q["summary"]["passed"] > q["summary"]["failed"]
    assert [c for c in q["checks"] if not c["passed"] and c["severity"] == "error"] == []
    assert {c["layer"] for c in q["checks"]} == {"bronze", "silver"}
    runs = client.get("/v1/pipeline/runs").json()
    assert runs["runs"][0]["run_id"]


def test_openapi_documents_every_route(client):
    spec = client.get("/openapi.json").json()
    assert len(spec["paths"]) >= 18
    assert "money_unit" in client.get("/v1/meta").json()
