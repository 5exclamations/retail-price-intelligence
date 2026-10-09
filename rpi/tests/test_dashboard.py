"""Dashboard: query layer and display formatting (pages themselves are exercised by the screenshot script)."""

from __future__ import annotations

import pytest

from rpi.dashboard import data as d
from rpi.tests.conftest import use_database

pytestmark = pytest.mark.slow


@pytest.mark.parametrize(
    ("qepik", "text"),
    [
        (349, "3.49 AZN"),
        (5, "0.05 AZN"),
        (100, "1.00 AZN"),
        (0, "0.00 AZN"),
        (-250, "-2.50 AZN"),
        (None, "-"),
    ],
)
def test_money_is_formatted_only_at_the_display_boundary(qepik, text):
    assert d.azn(qepik) == text


def test_basis_points_format():
    assert d.bp(2727) == "27.3%" and d.bp(None) == "-"


@pytest.fixture()
def env(built_db):
    with use_database(built_db["url"], built_db["landing"], built_db["truth"]):
        yield


def test_zone_rule_is_enforced_in_the_query_layer(env):
    assert d.zoned_choices() == {"absheron": ["A", "B", "C", "D"]}
    none_chosen = d.allowed_keys({})
    assert not any(k.startswith("absheron") for k in none_chosen)
    chosen = d.allowed_keys({"absheron": "C"})
    assert "absheron:C" in chosen and "absheron:A" not in chosen


def test_every_query_returns_data(env):
    keys = d.allowed_keys({"absheron": "A"})
    pid = int(d.products(min_retailers=4)["product_key"][0])
    assert d.meta()["as_of"].isoformat() == "2026-09-30"
    for name, frame in {
        "prices": d.current_prices(pid, keys),
        "history": d.history(pid, keys, 20),
        "competitiveness": d.competitiveness(keys),
        "promotions": d.promotions(keys),
        "promo_summary": d.promo_summary(),
        "index": d.price_index(),
        "movers": d.movers("up"),
        "basket": d.basket_latest(keys),
        "series": d.basket_series(keys),
        "items": d.basket_items(),
        "review": d.review_queue(),
        "methods": d.match_methods(),
        "dq": d.dq_latest(),
        "runs": d.runs(),
        "lineage": d.lineage(),
    }.items():
        assert frame.height > 0, name


def test_lineage_accounts_for_every_raw_row(env):
    lin = {r["stage"]: r["n"] for r in d.lineage().iter_rows(named=True)}
    assert (
        lin["bronze: raw price records"]
        == lin["silver: rejected records"] + lin["silver: price observations"]
    )
