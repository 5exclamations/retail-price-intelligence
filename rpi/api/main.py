"""Analytics REST API over the gold layer.

Conventions
* Money is integer qepik (1 AZN = 100 qepik) in every response; formatting belongs to the client.
* Every price carries ``observed_at``: no number without the time it was seen.
* A zoned retailer is never priced without a zone. Clients pass ``zones=retailer:zone`` (repeatable);
  zoned retailers without a selection are listed under ``excluded_price_points`` instead of guessed.
* Quarantined products do not exist as far as this API is concerned (gold excludes them).
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from psycopg import Connection
from psycopg.adapt import Loader
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from rpi import __version__
from rpi.config import get_settings
from rpi.matching.matcher import approve_review, reject_review


class _NumericAsFloat(Loader):
    """Indices and scores are ratios, not money (money is int qepik): give clients plain JSON numbers."""

    def load(self, data):
        return float(bytes(data))


def _configure(conn: Connection) -> None:
    conn.adapters.register_loader("numeric", _NumericAsFloat)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = ConnectionPool(get_settings().database_url, min_size=1, max_size=8, open=True,
                          configure=_configure, kwargs={"row_factory": dict_row, "autocommit": False})
    app.state.pool = pool
    try:
        yield
    finally:
        pool.close()


app = FastAPI(
    title="Retail Price Intelligence API",
    version=__version__,
    description="Read-only analytics over supermarket prices (synthetic demo data). "
                "Money fields are integer qepik; 100 qepik = 1 AZN.",
    lifespan=lifespan,
)


def get_conn() -> Iterator[Connection]:
    with app.state.pool.connection() as conn:
        yield conn
        conn.rollback()  # reads only, except review decisions which commit explicitly


Conn = Annotated[Connection, Depends(get_conn)]
Limit = Annotated[int, Query(ge=1, le=200)]


MIN_API_KEY_LENGTH = 16


def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Write endpoints fail closed.

    With no ``RPI_API_KEY`` configured (or a short one) writes are disabled outright, so a deployment
    that forgot to set a key is read-only instead of open. Comparison is constant time.
    """
    expected = os.environ.get("RPI_API_KEY", "")
    if len(expected) < MIN_API_KEY_LENGTH:
        raise HTTPException(503, f"write endpoints are disabled: set RPI_API_KEY (at least {MIN_API_KEY_LENGTH} characters)")
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(401, "missing or wrong X-API-Key", headers={"WWW-Authenticate": "ApiKey"})


# ------------------------------------------------------------------------------------ helpers


def parse_zones(conn: Connection, zones: list[str]) -> list[str]:
    """['absheron:B'] -> price point keys, validated against the warehouse."""
    valid = {r["price_point_key"] for r in conn.execute("SELECT price_point_key FROM gold.dim_price_point WHERE requires_zone_choice")}
    for z in zones:
        if z not in valid:
            raise HTTPException(422, f"unknown zoned price point {z!r}; valid: {sorted(valid)}")
    return zones


def split_price_points(conn: Connection, selected: list[str]):
    """(allowed keys, excluded rows). Zoned price points are allowed only when selected."""
    rows = conn.execute("SELECT price_point_key, retailer_code, label, requires_zone_choice FROM gold.dim_price_point ORDER BY 1").fetchall()
    allowed = [r["price_point_key"] for r in rows if not r["requires_zone_choice"] or r["price_point_key"] in selected]
    chosen_retailers = {r["retailer_code"] for r in rows if r["price_point_key"] in selected}
    excluded = [
        {"retailer_code": r["retailer_code"], "price_point_key": r["price_point_key"], "label": r["label"],
         "reason": "zone not selected" if r["retailer_code"] not in chosen_retailers else "other zone selected"}
        for r in rows if r["requires_zone_choice"] and r["price_point_key"] not in selected
    ]
    return allowed, excluded


ZonesParam = Annotated[list[str], Query(description="Zone selection for zoned retailers, repeatable, e.g. zones=absheron:B")]


# ------------------------------------------------------------------------------------ models


class PricePoint(BaseModel):
    price_point_key: str
    retailer_code: str
    retailer_name: str
    zone: str
    label: str
    requires_zone_choice: bool


class PriceRow(BaseModel):
    price_point_key: str
    label: str
    price_qepik: int
    regular_price_qepik: int
    old_price_qepik: int | None
    is_promo: bool
    available: bool
    observed_at: str
    days_since_observed: int
    is_cheapest: bool = False
    gap_to_cheapest_bp: int = Field(description="Premium over the cheapest price in basis points (100 = 1%)")


class ReviewDecision(BaseModel):
    decision: Literal["approve", "reject"]


class BasketRequest(BaseModel):
    product_ids: list[int] = Field(min_length=1, max_length=100)
    quantities: dict[int, int] | None = None
    zones: list[str] = []


# ------------------------------------------------------------------------------------ system


@app.get("/health", tags=["system"])
def health(conn: Conn) -> dict:
    conn.execute("SELECT 1")
    return {"status": "ok", "version": __version__}


@app.get("/v1/meta", tags=["system"])
def meta(conn: Conn) -> dict:
    """Data freshness: what the numbers in every other response are as of."""
    last = conn.execute("SELECT max(observed_date) AS d, max(observed_at) AS at FROM gold.fct_price_daily").fetchone()
    per = conn.execute(
        "SELECT retailer_code, max(observed_date) AS last_date, max(observed_at) AS last_observed_at "
        "FROM gold.fct_price_daily GROUP BY 1 ORDER BY 1").fetchall()
    run = conn.execute("SELECT run_id, status, finished_at FROM ops.pipeline_run ORDER BY started_at DESC LIMIT 1").fetchone()
    return {"data_as_of": last["d"], "last_observed_at": last["at"], "retailers": per, "last_pipeline_run": run,
            "currency": "AZN", "money_unit": "qepik (1 AZN = 100 qepik)", "data_is_synthetic": True}


@app.get("/v1/price-points", tags=["reference"], response_model=list[PricePoint])
def price_points(conn: Conn):
    return conn.execute(
        "SELECT price_point_key, retailer_code, retailer_name, price_zone AS zone, label, requires_zone_choice "
        "FROM gold.dim_price_point ORDER BY 1").fetchall()


@app.get("/v1/categories", tags=["reference"])
def categories(conn: Conn) -> list[str]:
    return [r["category"] for r in conn.execute("SELECT category FROM gold.dim_category ORDER BY 1")]


# ------------------------------------------------------------------------------------ products


@app.get("/v1/products", tags=["products"])
def list_products(conn: Conn, q: str | None = None, category: str | None = None,
                  min_retailers: Annotated[int, Query(ge=1, le=10)] = 1, limit: Limit = 50,
                  offset: Annotated[int, Query(ge=0)] = 0) -> dict:
    where, params = ["retailer_count >= %s"], [min_retailers]
    if q:
        where.append("lower(product_name) LIKE %s")
        params.append(f"%{q.lower()}%")
    if category:
        where.append("category = %s")
        params.append(category)
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) AS n FROM gold.dim_product WHERE {clause}", params).fetchone()["n"]
    rows = conn.execute(
        f"""SELECT product_key AS id, product_name AS name, brand, category, unit_type, unit_value, is_weighed, retailer_count
            FROM gold.dim_product WHERE {clause} ORDER BY retailer_count DESC, product_name LIMIT %s OFFSET %s""",
        [*params, limit, offset]).fetchall()
    return {"total": total, "items": rows}


@app.get("/v1/products/{product_id}", tags=["products"])
def product(product_id: int, conn: Conn) -> dict:
    row = conn.execute(
        "SELECT product_key AS id, product_name AS name, brand, category, unit_type, unit_value, pack, is_weighed, ean, retailer_count "
        "FROM gold.dim_product WHERE product_key = %s", (product_id,)).fetchone()
    if not row:
        raise HTTPException(404, "product not found (or quarantined)")
    return row


@app.get("/v1/products/{product_id}/prices", tags=["products"])
def product_prices(product_id: int, conn: Conn, zones: ZonesParam = [], max_age_days: Annotated[int, Query(ge=0, le=30)] = 3,
                   include_unavailable: bool = False) -> dict:
    """Current price at every price point that sells the product, cheapest first."""
    product(product_id, conn)
    selected = parse_zones(conn, zones)
    allowed, excluded = split_price_points(conn, selected)
    rows = conn.execute(
        """SELECT c.price_point_key, pp.label, min(c.price_qepik) AS price_qepik,
                  min(c.regular_price_qepik) AS regular_price_qepik, min(c.old_price_qepik) AS old_price_qepik,
                  bool_or(c.is_promo) AS is_promo, bool_or(c.available) AS available,
                  max(c.observed_at) AS observed_at, min(c.days_since_observed) AS days_since_observed
           FROM gold.mart_price_current c JOIN gold.dim_price_point pp USING (price_point_key)
           WHERE c.product_key = %s AND c.price_point_key = ANY(%s) AND c.days_since_observed <= %s
           GROUP BY 1, 2 ORDER BY min(c.price_qepik)""", (product_id, allowed, max_age_days)).fetchall()
    if not include_unavailable:
        rows = [r for r in rows if r["available"]]
    cheapest = min((r["price_qepik"] for r in rows), default=None)
    for r in rows:
        r["observed_at"] = r["observed_at"].isoformat()
        r["is_cheapest"] = r["price_qepik"] == cheapest
        r["gap_to_cheapest_bp"] = round((r["price_qepik"] - cheapest) * 10000 / cheapest) if cheapest else 0
    return {"product_id": product_id, "prices": rows, "excluded_price_points": excluded,
            "note": "Zoned retailers appear only for the zones passed in `zones`."}


@app.get("/v1/products/{product_id}/history", tags=["products"])
def product_history(product_id: int, conn: Conn, price_point: str, days: Annotated[int, Query(ge=2, le=365)] = 60) -> dict:
    product(product_id, conn)
    rows = conn.execute(
        """SELECT f.observed_date, min(f.price_qepik) AS price_qepik, min(f.regular_price_qepik) AS regular_price_qepik,
                  bool_or(f.is_promo) AS is_promo, max(f.observed_at) AS observed_at
           FROM gold.fct_price_daily f JOIN gold.dim_store_item b USING (store_item_id)
           WHERE b.product_key = %s AND f.price_point_key = %s
             AND f.observed_date > (SELECT max(observed_date) FROM gold.fct_price_daily) - %s
           GROUP BY 1 ORDER BY 1""", (product_id, price_point, days)).fetchall()
    if not rows:
        raise HTTPException(404, "no history for this product at that price point")
    for r in rows:
        r["observed_at"] = r["observed_at"].isoformat()
    return {"product_id": product_id, "price_point_key": price_point, "series": rows}


# ------------------------------------------------------------------------------------ analytics


@app.get("/v1/promotions", tags=["analytics"])
def promotions(conn: Conn, zones: ZonesParam = [], category: str | None = None, retailer: str | None = None,
               flagged: Annotated[Literal["any", "inflated", "fake", "honest"], Query()] = "any",
               min_real_discount_bp: int | None = None, limit: Limit = 50) -> dict:
    """Promotions on the latest observed day, judged against the market rather than the shelf tag."""
    allowed, excluded = split_price_points(conn, parse_zones(conn, zones))
    where = ["a.observed_date = (SELECT max(observed_date) FROM gold.mart_promo_analysis)", "a.price_point_key = ANY(%s)", "a.available"]
    params: list = [allowed]
    if category:
        where.append("a.category = %s"); params.append(category)
    if retailer:
        where.append("a.retailer_code = %s"); params.append(retailer)
    if flagged == "inflated":
        where.append("a.inflated_flag")
    elif flagged == "fake":
        where.append("a.fake_flag")
    elif flagged == "honest":
        where.append("a.ref_reliable AND NOT a.inflated_flag AND NOT a.fake_flag")
    if min_real_discount_bp is not None:
        where.append("a.real_discount_bp >= %s"); params.append(min_real_discount_bp)
    rows = conn.execute(
        f"""SELECT a.product_key AS product_id, p.product_name, a.category, a.price_point_key, pp.label,
                   a.price_qepik, a.old_price_qepik, a.market_ref_qepik, a.claimed_discount_bp, a.real_discount_bp,
                   a.ref_reliable, a.inflated_flag, a.fake_flag, a.observed_at::text AS observed_at
            FROM gold.mart_promo_analysis a JOIN gold.dim_product p ON p.product_key = a.product_key
            JOIN gold.dim_price_point pp ON pp.price_point_key = a.price_point_key
            WHERE {' AND '.join(where)}
            ORDER BY a.real_discount_bp DESC NULLS LAST LIMIT %s""", [*params, limit]).fetchall()
    return {"items": rows, "excluded_price_points": excluded,
            "method": "real discount = (median ordinary price at other retailers that day - price) / that median"}


@app.get("/v1/analytics/competitiveness", tags=["analytics"])
def competitiveness(conn: Conn, weeks: Annotated[int, Query(ge=1, le=52)] = 8) -> dict:
    rows = conn.execute(
        """SELECT c.week_start, c.price_point_key, pp.label, c.retailer_code, c.n_products, c.price_index_vs_market,
                  c.share_cheapest_pct, c.promo_share_pct
           FROM gold.mart_retailer_competitiveness c JOIN gold.dim_price_point pp USING (price_point_key)
           WHERE c.week_start > (SELECT max(week_start) FROM gold.mart_retailer_competitiveness) - make_interval(weeks => %s)
           ORDER BY c.week_start, c.price_point_key""", (weeks,)).fetchall()
    return {"items": rows, "definition": "price_index_vs_market: 100 = market median (geometric mean over products sold by >= 3 retailers)"}


@app.get("/v1/analytics/price-index", tags=["analytics"])
def price_index(conn: Conn, category: str = "All categories") -> dict:
    rows = conn.execute(
        "SELECT week_start, category, index_regular, index_effective, wow_change_pct, n_items "
        "FROM gold.mart_price_index WHERE category = %s ORDER BY week_start", (category,)).fetchall()
    if not rows:
        raise HTTPException(404, f"no index for category {category!r}")
    return {"category": category, "base_week": rows[0]["week_start"], "series": rows,
            "methodology": "docs/METHODOLOGY.md: fixed-base matched-model Jevons, equal weights, base week = 100"}


@app.get("/v1/analytics/category-trends", tags=["analytics"])
def category_trends(conn: Conn) -> dict:
    rows = conn.execute(
        """SELECT category, week_start, index_regular, index_effective, wow_change_pct, promo_share_pct, median_real_bp
           FROM gold.mart_category_trend WHERE category <> 'All categories' ORDER BY category, week_start""").fetchall()
    return {"items": rows}


@app.get("/v1/analytics/price-movers", tags=["analytics"])
def price_movers(conn: Conn, direction: Literal["up", "down"] = "up", category: str | None = None, limit: Limit = 20) -> dict:
    order = "DESC" if direction == "up" else "ASC"
    where, params = ["true"], []
    if category:
        where.append("category = %s"); params.append(category)
    rows = conn.execute(
        f"""SELECT product_key AS product_id, product_name, category, retailer_code, price_28d_ago_qepik, price_now_qepik,
                   change_bp, observed_date FROM gold.mart_price_movers WHERE {' AND '.join(where)}
            ORDER BY change_bp {order}, product_key LIMIT %s""", [*params, limit]).fetchall()
    return {"items": rows, "window_days": 28}


@app.get("/v1/basket/default", tags=["basket"])
def default_basket(conn: Conn, zones: ZonesParam = []) -> dict:
    """Cost of the default basket (see warehouse/models/marts/basket_definition.sql) on the latest day."""
    allowed, excluded = split_price_points(conn, parse_zones(conn, zones))
    day = conn.execute("SELECT max(observed_date) AS d FROM gold.mart_cheapest_basket").fetchone()["d"]
    rows = conn.execute(
        """SELECT b.price_point_key, pp.label, b.total_qepik, b.items_total, b.items_available, b.is_complete
           FROM gold.mart_cheapest_basket b JOIN gold.dim_price_point pp USING (price_point_key)
           WHERE b.observed_date = %s AND b.price_point_key = ANY(%s) ORDER BY b.is_complete DESC, b.total_qepik""",
        (day, allowed)).fetchall()
    complete = [r for r in rows if r["is_complete"]]
    if complete:
        best = complete[0]["total_qepik"]
        for r in complete:
            r["premium_bp"] = round((r["total_qepik"] - best) * 10000 / best)
    items = conn.execute(
        "SELECT product_key AS product_id, product_name, category, quantity FROM gold.basket_definition ORDER BY category, product_key"
    ).fetchall()
    return {"observed_date": day, "cheapest": complete[0]["price_point_key"] if complete else None,
            "price_points": rows, "items": items, "excluded_price_points": excluded,
            "note": "Only price points that stock every item are ranked; incomplete ones are shown without a rank."}


@app.post("/v1/basket/cheapest", tags=["basket"])
def cheapest_basket(req: BasketRequest, conn: Conn, max_age_days: int = 3) -> dict:
    """Cheapest way to buy a custom list: best single store, and the best split across stores."""
    allowed, excluded = split_price_points(conn, parse_zones(conn, req.zones))
    qty = {pid: (req.quantities or {}).get(pid, 1) for pid in req.product_ids}
    if any(q < 1 or q > 99 for q in qty.values()):
        raise HTTPException(422, "quantities must be 1..99")
    known = {r["product_key"] for r in conn.execute("SELECT product_key FROM gold.dim_product WHERE product_key = ANY(%s)", (req.product_ids,))}
    missing = sorted(set(req.product_ids) - known)
    if missing:
        raise HTTPException(404, f"unknown or quarantined product ids: {missing}")
    rows = conn.execute(
        """SELECT product_key, price_point_key, min(price_qepik) AS price_qepik, max(observed_at) AS observed_at
           FROM gold.mart_price_current
           WHERE product_key = ANY(%s) AND price_point_key = ANY(%s) AND available AND days_since_observed <= %s
           GROUP BY 1, 2""", (list(qty), allowed, max_age_days)).fetchall()
    by_pp: dict[str, dict[int, int]] = {}
    seen_at: dict[str, str] = {}
    for r in rows:
        by_pp.setdefault(r["price_point_key"], {})[r["product_key"]] = r["price_qepik"]
        seen_at[r["price_point_key"]] = max(seen_at.get(r["price_point_key"], ""), r["observed_at"].isoformat())
    singles = []
    for key, prices in by_pp.items():
        missing_items = [p for p in qty if p not in prices]
        singles.append({"price_point_key": key, "total_qepik": sum(prices[p] * q for p, q in qty.items() if p in prices),
                        "items_missing": missing_items, "is_complete": not missing_items, "latest_observation": seen_at[key]})
    singles.sort(key=lambda s: (not s["is_complete"], s["total_qepik"]))
    split, split_total = [], 0
    for p, q in qty.items():
        options = [(prices[p], key) for key, prices in by_pp.items() if p in prices]
        if options:
            price, key = min(options)
            split.append({"product_id": p, "price_point_key": key, "price_qepik": price, "quantity": q})
            split_total += price * q
    best_single = next((s for s in singles if s["is_complete"]), None)
    return {"single_store": singles, "best_single_store": best_single,
            "split": {"lines": split, "total_qepik": split_total, "complete": len(split) == len(qty)},
            "split_saving_qepik": (best_single["total_qepik"] - split_total) if best_single and len(split) == len(qty) else None,
            "excluded_price_points": excluded}


# ------------------------------------------------------------------------------------ matching + quality


@app.get("/v1/matching/review-queue", tags=["matching"])
def review_queue(conn: Conn, limit: Limit = 25) -> dict:
    rows = conn.execute(
        """SELECT rv.id, rv.score, rv.features, si.retailer_code, si.name_raw AS item_name,
                  p.id AS candidate_product_id, p.name AS candidate_name
           FROM silver.match_review rv JOIN silver.store_item si ON si.id = rv.store_item_id
           JOIN silver.product p ON p.id = rv.candidate_product_id
           WHERE rv.status = 'pending' ORDER BY rv.score DESC, rv.id LIMIT %s""", (limit,)).fetchall()
    total = conn.execute("SELECT count(*) AS n FROM silver.match_review WHERE status = 'pending'").fetchone()["n"]
    for r in rows:
        r["score"] = float(r["score"])
    return {"pending_total": total, "items": rows}


@app.post("/v1/matching/review/{review_id}", tags=["matching"], dependencies=[Depends(require_key)])
def decide_review(review_id: int, body: ReviewDecision, conn: Conn) -> dict:
    """Approve or reject a candidate match. Run the pipeline afterwards to refresh gold."""
    try:
        out = (approve_review if body.decision == "approve" else reject_review)(conn, review_id, "api")
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    conn.commit()
    return out


@app.get("/v1/quality/latest", tags=["quality"])
def quality_latest(conn: Conn) -> dict:
    run = conn.execute("SELECT run_id, status, as_of_date, finished_at FROM ops.pipeline_run ORDER BY started_at DESC LIMIT 1").fetchone()
    if not run:
        return {"run": None, "checks": []}
    checks = conn.execute(
        "SELECT layer, check_name, scope, severity, passed, observed, threshold, detail FROM ops.dq_result "
        "WHERE run_id = %s ORDER BY passed, severity, layer, check_name", (run["run_id"],)).fetchall()
    return {"run": run, "summary": {"passed": sum(c["passed"] for c in checks), "failed": sum(not c["passed"] for c in checks)},
            "checks": checks}


@app.get("/v1/pipeline/runs", tags=["quality"])
def pipeline_runs(conn: Conn, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict:
    runs = conn.execute("SELECT run_id, flow_name, status, as_of_date, started_at, finished_at, error FROM ops.pipeline_run "
                        "ORDER BY started_at DESC LIMIT %s", (limit,)).fetchall()
    alerts = conn.execute("SELECT run_id, severity, source, title, detail, created_at FROM ops.alert ORDER BY id DESC LIMIT 20").fetchall()
    return {"runs": runs, "recent_alerts": alerts}
