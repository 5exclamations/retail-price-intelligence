"""Streamlit analytics dashboard (synthetic demo data).

Run:  streamlit run rpi/dashboard/app.py
Rules carried over from the product: money is formatted only here, every number shows when it was observed,
and a zoned retailer is never priced until a zone is chosen (sidebar).
"""

from __future__ import annotations

import os

import plotly.express as px
import polars as pl
import streamlit as st

from rpi.dashboard import data as d
from rpi.matching.matcher import approve_review, reject_review

REVIEW_WRITES = os.environ.get("RPI_DASHBOARD_ALLOW_REVIEW") == "1"

st.set_page_config(page_title="Retail Price Intelligence", page_icon="🛒", layout="wide")

BASE_COLORS = {
    "baku_fresh": "#0072B2",
    "caspianmart": "#009E73",
    "absheron": "#E69F00",
    "shirvan": "#CC79A7",
    "sumqayit": "#D55E00",
}
ZONE_SHADES = {"A": "#E69F00", "B": "#B87F00", "C": "#8A5F00", "D": "#F2C14E"}


def color_map(pp: pl.DataFrame) -> dict[str, str]:
    out = {}
    for r in pp.iter_rows(named=True):
        out[r["label"]] = (
            ZONE_SHADES.get(r["price_zone"], BASE_COLORS.get(r["retailer_code"], "#888"))
            if r["requires_zone_choice"]
            else BASE_COLORS.get(r["retailer_code"], "#888")
        )
    return out


def styled(fig, height: int = 420):
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", y=-0.2),
        font=dict(size=13),
    )
    return fig


# ---------------------------------------------------------------------------- shared sidebar


def sidebar() -> dict[str, str]:
    st.sidebar.markdown("### Shopper context")
    st.sidebar.caption(
        "Zoned chains have a different price list per zone, so their prices are shown only for the zone you choose."
    )
    selected = {}
    pp = d.price_points()
    names = dict(zip(pp["retailer_code"].to_list(), pp["retailer_name"].to_list(), strict=False))
    for retailer, zones in d.zoned_choices().items():
        selected[retailer] = st.sidebar.selectbox(
            f"{names[retailer]} price zone", zones, key=f"zone_{retailer}"
        )
    m = d.meta()
    st.sidebar.divider()
    if m:
        st.sidebar.markdown(
            f"**Data as of** {m['as_of']}  \nlast observation {m['last_observed_at']:%Y-%m-%d %H:%M} UTC"
        )
    st.sidebar.info("All data is synthetic. Retailers, brands and prices are fictional.")
    return selected


def footer() -> None:
    m = d.meta()
    if m:
        st.caption(
            f"Observed up to {m['last_observed_at']:%Y-%m-%d %H:%M} UTC · synthetic demo data · money shown in AZN, stored as integer qepik"
        )


# ---------------------------------------------------------------------------- pages


def page_overview():
    st.title("Retail Price Intelligence")
    st.caption("Price comparison, promotions and price indices across supermarket chains.")
    zones = st.session_state["zones"]
    keys = d.allowed_keys(zones)
    idx = d.price_index().filter(pl.col("category") == "All categories")
    basket = d.basket_latest(keys).filter(pl.col("is_complete"))
    promos = d.promotions(keys)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Products compared",
        f"{d.query('SELECT count(*) AS n FROM gold.dim_product WHERE retailer_count >= 2')['n'][0]:,}",
    )
    c2.metric(
        "Price index (base week = 100)",
        f"{idx['index_regular'][-1]:.1f}" if idx.height else "-",
        f"{idx['wow_change_pct'][-1]:+.2f}% w/w"
        if idx.height and idx["wow_change_pct"][-1] is not None
        else None,
    )
    c3.metric(
        "Cheapest basket",
        d.azn(basket["total_qepik"][0]) if basket.height else "-",
        basket["label"][0] if basket.height else None,
        delta_color="off",
    )
    honest = (
        promos.filter(
            pl.col("ref_reliable") & ~pl.col("inflated_flag") & ~pl.col("fake_flag")
        ).height
        if promos.height
        else 0
    )
    c4.metric(
        "Promotions today",
        f"{promos.height:,}",
        f"{honest} verified against the market",
        delta_color="off",
    )

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Best verified deals today")
        st.caption(
            "Ranked by discount against the market's ordinary price, not against the shelf tag."
        )
        if promos.height:
            best = promos.filter(
                pl.col("ref_reliable") & ~pl.col("inflated_flag") & ~pl.col("fake_flag")
            ).head(12)
            st.dataframe(
                best.select(
                    pl.col("product_name").alias("Product"),
                    pl.col("label").alias("Where"),
                    pl.col("price_qepik")
                    .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
                    .alias("Price"),
                    pl.col("market_ref_qepik")
                    .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
                    .alias("Market price"),
                    pl.col("real_discount_bp")
                    .map_elements(d.bp, return_dtype=pl.Utf8, skip_nulls=False)
                    .alias("Real discount"),
                    pl.col("observed_at").dt.strftime("%Y-%m-%d %H:%M").alias("Observed"),
                ),
                hide_index=True,
                use_container_width=True,
            )
    with right:
        st.subheader("Basket cost by store")
        if basket.height:
            fig = px.bar(
                basket.with_columns((pl.col("total_qepik") / 100).alias("AZN")),
                x="AZN",
                y="label",
                orientation="h",
                color="label",
                color_discrete_map=color_map(d.price_points()),
                text="AZN",
            )
            fig.update_traces(texttemplate="%{text:.2f}")
            fig.update_layout(showlegend=False, yaxis_title=None, xaxis_title="AZN")
            st.plotly_chart(styled(fig, 380), use_container_width=True)
    footer()


def page_compare():
    st.title("Price comparison")
    zones = st.session_state["zones"]
    keys = d.allowed_keys(zones)
    c1, c2 = st.columns([2, 1])
    search = c1.text_input("Search products", "")
    cat = c2.selectbox("Category", ["All", *d.categories()])
    prods = d.products(search, cat, min_retailers=2)
    if prods.height == 0:
        st.info("No product matches.")
        return
    choice = st.selectbox(
        "Product",
        prods["product_key"].to_list(),
        format_func=lambda k: (
            f"{prods.filter(pl.col('product_key') == k)['product_name'][0]}  ·  {prods.filter(pl.col('product_key') == k)['retailer_count'][0]} chains"
        ),
    )
    prices = d.current_prices(choice, keys).filter(pl.col("available"))
    if prices.height == 0:
        st.warning("No current price for the chosen zone(s).")
        return
    cheapest = prices["price_qepik"].min()
    prices = prices.with_columns(
        (pl.col("price_qepik") / 100).alias("AZN"),
        pl.when(pl.col("is_promo"))
        .then(pl.lit("promotion"))
        .otherwise(pl.lit("regular"))
        .alias("type"),
    )
    fig = px.bar(
        prices,
        x="AZN",
        y="label",
        orientation="h",
        color="label",
        color_discrete_map=color_map(d.price_points()),
        text="AZN",
        pattern_shape="type",
        pattern_shape_map={"promotion": "/", "regular": ""},
    )
    fig.update_traces(texttemplate="%{text:.2f}")
    fig.update_layout(
        showlegend=False,
        yaxis_title=None,
        xaxis_title="AZN",
        yaxis=dict(categoryorder="total descending"),
    )
    st.plotly_chart(styled(fig, 80 + 55 * prices.height), use_container_width=True)
    table = prices.select(
        pl.col("label").alias("Store"),
        pl.col("price_qepik")
        .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
        .alias("Price"),
        pl.col("regular_price_qepik")
        .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
        .alias("Regular"),
        pl.col("old_price_qepik")
        .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
        .alias("Shelf 'was' price"),
        pl.col("type").alias("Type"),
        ((pl.col("price_qepik") - cheapest) * 10000 // cheapest)
        .map_elements(d.bp, return_dtype=pl.Utf8, skip_nulls=False)
        .alias("Above cheapest"),
        pl.col("observed_at").dt.strftime("%Y-%m-%d %H:%M").alias("Observed (UTC)"),
    )
    st.dataframe(table, hide_index=True, use_container_width=True)
    footer()


def page_history():
    st.title("Price history")
    zones = st.session_state["zones"]
    pp = d.price_points()
    keys = d.allowed_keys(zones)
    prods = d.products("", None, min_retailers=3, limit=800)
    choice = st.selectbox(
        "Product",
        prods["product_key"].to_list(),
        format_func=lambda k: prods.filter(pl.col("product_key") == k)["product_name"][0],
    )
    days = st.slider("Window (days)", 14, 90, 60)
    h = d.history(choice, keys, days)
    if h.height == 0:
        st.info("No history.")
        return
    h = h.with_columns(
        (pl.col("price_qepik") / 100).alias("Price (AZN)"),
        (pl.col("regular_price_qepik") / 100).alias("Regular (AZN)"),
    )
    fig = px.line(
        h,
        x="observed_date",
        y="Price (AZN)",
        color="label",
        color_discrete_map=color_map(pp),
        markers=False,
    )
    promo = h.filter(pl.col("is_promo"))
    if promo.height:
        fig.add_scatter(
            x=promo["observed_date"],
            y=promo["Price (AZN)"],
            mode="markers",
            name="promotion day",
            marker=dict(symbol="diamond", size=8, color="black"),
        )
    fig.update_layout(xaxis_title=None)
    st.plotly_chart(styled(fig, 460), use_container_width=True)
    st.caption(
        "Lines are the price on the shelf; diamonds mark promotion days. Gaps are days a retailer's feed was missing, not zero prices."
    )
    fig2 = px.line(
        h, x="observed_date", y="Regular (AZN)", color="label", color_discrete_map=color_map(pp)
    )
    fig2.update_layout(xaxis_title=None, title="Regular price (promotions removed)")
    st.plotly_chart(styled(fig2, 340), use_container_width=True)
    footer()


def page_competitiveness():
    st.title("Retailer competitiveness")
    keys = d.allowed_keys(st.session_state["zones"])
    c = d.competitiveness(keys)
    if c.height == 0:
        st.info("Not enough data.")
        return
    pp = d.price_points()
    st.caption(
        "Price index vs market: 100 is the median price of products sold by at least three chains. Lower is cheaper."
    )
    fig = px.line(
        c,
        x="week_start",
        y="price_index_vs_market",
        color="label",
        color_discrete_map=color_map(pp),
        markers=True,
    )
    fig.add_hline(y=100, line_dash="dot", line_color="#888")
    fig.update_layout(xaxis_title=None, yaxis_title="Price index (market = 100)")
    st.plotly_chart(styled(fig), use_container_width=True)
    latest = c.filter(pl.col("week_start") == c["week_start"].max()).sort("price_index_vs_market")
    fig2 = px.bar(
        latest,
        x="label",
        y="share_cheapest_pct",
        color="label",
        color_discrete_map=color_map(pp),
        text="share_cheapest_pct",
    )
    fig2.update_traces(texttemplate="%{text:.0f}%")
    fig2.update_layout(
        showlegend=False,
        xaxis_title=None,
        yaxis_title="% of products where this store is cheapest",
        title=f"Share of products where the store is cheapest, week of {latest['week_start'][0]}",
    )
    st.plotly_chart(styled(fig2, 360), use_container_width=True)
    st.dataframe(
        latest.select(
            pl.col("label").alias("Store"),
            pl.col("n_products").alias("Products compared"),
            pl.col("price_index_vs_market").alias("Price index"),
            pl.col("share_cheapest_pct").alias("Cheapest %"),
            pl.col("promo_share_pct").alias("On promotion %"),
        ),
        hide_index=True,
        use_container_width=True,
    )
    footer()


def page_promotions():
    st.title("Promotion analysis")
    st.caption(
        "A promotion is judged against what other chains charge for the same product, not against the retailer's own 'was' price."
    )
    keys = d.allowed_keys(st.session_state["zones"])
    p = d.promotions(keys, limit=2000)
    s = d.promo_summary()
    if p.height == 0:
        st.info("No promotions on the latest day.")
        return
    c1, c2, c3 = st.columns(3)
    reliable = p.filter(pl.col("ref_reliable"))
    c1.metric("Promotions on the latest day", f"{p.height:,}")
    c2.metric(
        "Inflated 'was' price",
        f"{100 * reliable['inflated_flag'].mean():.1f}%" if reliable.height else "-",
        help="Claimed discount exceeds the market-based discount by more than 15 percentage points.",
    )
    c3.metric(
        "No real saving",
        f"{100 * reliable['fake_flag'].mean():.1f}%" if reliable.height else "-",
        help="Promotion price is not below the market's ordinary price.",
    )
    pdf = reliable.with_columns(
        (pl.col("claimed_discount_bp") / 100).alias("Claimed discount %"),
        (pl.col("real_discount_bp") / 100).alias("Real discount %"),
        pl.when(pl.col("inflated_flag"))
        .then(pl.lit("inflated"))
        .otherwise(pl.lit("consistent"))
        .alias("verdict"),
    )
    fig = px.scatter(
        pdf,
        x="Claimed discount %",
        y="Real discount %",
        color="verdict",
        hover_name="product_name",
        hover_data=["label"],
        color_discrete_map={"inflated": "#D55E00", "consistent": "#0072B2"},
        opacity=0.7,
    )
    fig.add_shape(type="line", x0=0, y0=0, x1=60, y1=60, line=dict(dash="dot", color="#888"))
    st.plotly_chart(styled(fig, 460), use_container_width=True)
    st.caption(
        "Points on the dotted line are honest: the claimed and the market-based discounts agree. Points below it are overstated."
    )
    st.subheader("By retailer (all weeks)")
    st.dataframe(
        s.select(
            pl.col("retailer_code").alias("Retailer"),
            pl.col("promo_price_days").alias("Promo price-days"),
            pl.col("median_claimed_bp")
            .map_elements(d.bp, return_dtype=pl.Utf8, skip_nulls=False)
            .alias("Median claimed"),
            pl.col("median_real_bp")
            .map_elements(d.bp, return_dtype=pl.Utf8, skip_nulls=False)
            .alias("Median real"),
            pl.col("inflated_share_pct").round(1).alias("Inflated %"),
            pl.col("fake_share_pct").round(1).alias("No saving %"),
        ),
        hide_index=True,
        use_container_width=True,
    )
    st.subheader("Most overstated promotions today")
    worst = (
        p.filter(pl.col("inflated_flag"))
        .with_columns((pl.col("claimed_discount_bp") - pl.col("real_discount_bp")).alias("gap"))
        .sort("gap", descending=True)
        .head(10)
    )
    st.dataframe(
        worst.select(
            pl.col("product_name").alias("Product"),
            pl.col("label").alias("Where"),
            pl.col("price_qepik")
            .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
            .alias("Price"),
            pl.col("old_price_qepik")
            .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
            .alias("Shelf 'was'"),
            pl.col("market_ref_qepik")
            .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
            .alias("Market price"),
            pl.col("claimed_discount_bp")
            .map_elements(d.bp, return_dtype=pl.Utf8, skip_nulls=False)
            .alias("Claimed"),
            pl.col("real_discount_bp")
            .map_elements(d.bp, return_dtype=pl.Utf8, skip_nulls=False)
            .alias("Real"),
        ),
        hide_index=True,
        use_container_width=True,
    )
    footer()


def page_trends():
    st.title("Category trends and price index")
    idx = d.price_index()
    cats = idx["category"].unique().sort().to_list()
    pick = st.multiselect(
        "Categories",
        cats,
        default=["All categories", "Dairy & Eggs", "Meat & Poultry", "Fruit & Vegetables"],
    )
    variant = st.radio(
        "Index variant",
        ["Regular price (promotions removed)", "Effective price (what the shelf shows)"],
        horizontal=True,
    )
    col = "index_regular" if variant.startswith("Regular") else "index_effective"
    sub = idx.filter(pl.col("category").is_in(pick))
    fig = px.line(sub, x="week_start", y=col, color="category", markers=True)
    fig.add_hline(y=100, line_dash="dot", line_color="#888")
    fig.update_layout(xaxis_title=None, yaxis_title="Index (first complete week = 100)")
    st.plotly_chart(styled(fig, 460), use_container_width=True)
    with st.expander("Methodology"):
        st.markdown(
            "Fixed-base **matched-model Jevons** index. For every (product, retailer) pair priced in the base week, take the "
            "ratio of its weekly average price to its base-week price; the index is the **geometric mean** of those ratios "
            "x 100, with equal weights. Zoned chains are averaged over zones first, so a chain counts once. Weeks with fewer than "
            "4 observed days are dropped. It is *inflation-like*, not an official CPI: there are no expenditure weights and "
            "no quality adjustment. Details: `docs/METHODOLOGY.md`."
        )
    st.subheader("Movers (regular price, last 28 days)")
    left, right = st.columns(2)
    for col_, direction, title in (
        (left, "up", "Biggest increases"),
        (right, "down", "Biggest decreases"),
    ):
        m = d.movers(direction)
        col_.markdown(f"**{title}**")
        col_.dataframe(
            m.select(
                pl.col("product_name").alias("Product"),
                pl.col("retailer_code").alias("Retailer"),
                pl.col("price_28d_ago_qepik")
                .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
                .alias("28 days ago"),
                pl.col("price_now_qepik")
                .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
                .alias("Now"),
                pl.col("change_bp")
                .map_elements(d.bp, return_dtype=pl.Utf8, skip_nulls=False)
                .alias("Change"),
            ),
            hide_index=True,
            use_container_width=True,
        )
    footer()


def page_basket():
    st.title("Cheapest shopping basket")
    keys = d.allowed_keys(st.session_state["zones"])
    pp = d.price_points()
    items = d.basket_items()
    latest = d.basket_latest(keys)
    st.caption(
        f"Default basket: {items.height} products stocked by every chain (best-covered products per category). Only stores that stock the whole basket are ranked."
    )
    complete = latest.filter(pl.col("is_complete"))
    if complete.height:
        best = complete["total_qepik"][0]
        fig = px.bar(
            complete.with_columns(
                (pl.col("total_qepik") / 100).alias("AZN"),
                ((pl.col("total_qepik") - best) * 10000 // best / 100).alias("above"),
            ),
            x="AZN",
            y="label",
            orientation="h",
            color="label",
            color_discrete_map=color_map(pp),
            text="AZN",
            hover_data=["above"],
        )
        fig.update_traces(texttemplate="%{text:.2f}")
        fig.update_layout(
            showlegend=False, yaxis_title=None, yaxis=dict(categoryorder="total descending")
        )
        st.plotly_chart(styled(fig, 80 + 60 * complete.height), use_container_width=True)
        spread = complete["total_qepik"].max() - best
        st.success(
            f"{complete['label'][0]} is cheapest. Choosing the dearest complete store instead costs {d.azn(spread)} more on this basket."
        )
    incomplete = latest.filter(~pl.col("is_complete"))
    if incomplete.height:
        st.caption(
            "Not ranked (missing items today): "
            + ", ".join(
                f"{r['label']} ({r['items_available']}/{r['items_total']})"
                for r in incomplete.iter_rows(named=True)
            )
        )
    series = d.basket_series(keys)
    if series.height:
        fig2 = px.line(
            series.with_columns((pl.col("total_qepik") / 100).alias("AZN")),
            x="observed_date",
            y="AZN",
            color="label",
        )
        fig2.update_layout(
            xaxis_title=None, yaxis_title="Basket cost (AZN)", title="Basket cost over time"
        )
        st.plotly_chart(styled(fig2, 380), use_container_width=True)
    st.subheader("Build your own basket")
    prods = d.products("", None, min_retailers=3, limit=800)
    names = dict(zip(prods["product_key"].to_list(), prods["product_name"].to_list(), strict=False))
    chosen = st.multiselect(
        "Products",
        list(names),
        format_func=lambda k: names[k],
        default=items["product_key"].to_list()[:3],
    )
    if chosen:
        rows = d.custom_basket(chosen, keys)
        by = (
            rows.group_by("label")
            .agg(pl.col("price_qepik").sum().alias("total"), pl.len().alias("n"))
            .sort("total")
        )
        full = by.filter(pl.col("n") == len(chosen))
        if full.height:
            st.dataframe(
                full.select(
                    pl.col("label").alias("Store"),
                    pl.col("total")
                    .map_elements(d.azn, return_dtype=pl.Utf8, skip_nulls=False)
                    .alias("Total"),
                ),
                hide_index=True,
                use_container_width=True,
            )
            split = (
                rows.group_by("product_key")
                .agg(pl.col("price_qepik").min())
                .select(pl.col("price_qepik").sum())[0, 0]
            )
            st.info(
                f"Buying each item at its cheapest store would cost {d.azn(split)} (saves {d.azn(full['total'][0] - split)} vs the best single store)."
            )
        else:
            st.warning("No single store stocks all selected products right now.")
    st.dataframe(
        items.select(
            pl.col("product_name").alias("Default basket item"),
            pl.col("category").alias("Category"),
            pl.col("quantity").alias("Qty"),
        ),
        hide_index=True,
        use_container_width=True,
    )
    footer()


def page_review():
    st.title("Product matching review queue")
    st.caption(
        "Uncertain matches wait here. Approving merges the item into the candidate product; run the pipeline afterwards to refresh gold."
    )
    if not REVIEW_WRITES:
        st.info(
            "Read-only: set RPI_DASHBOARD_ALLOW_REVIEW=1 on a trusted, local dashboard to enable the buttons. "
            "Anyone who can reach an enabled dashboard can change matches."
        )
    q = d.review_queue()
    left, right = st.columns([2, 1])
    with right:
        st.markdown("**How items were matched**")
        st.dataframe(d.match_methods(), hide_index=True, use_container_width=True)
        qt = d.quarantined()
        st.markdown(f"**Quarantined products: {qt.height}** (hidden from every price view)")
        if qt.height:
            st.dataframe(qt, hide_index=True, use_container_width=True)
    with left:
        if q.height == 0:
            st.success("The queue is empty.")
        for r in q.head(10).iter_rows(named=True):
            with st.container(border=True):
                st.markdown(
                    f"**{r['retailer_code']}**: `{r['item_name']}`  \nmay be the same as  \n**{r['candidate_name']}**  \nscore {r['score']:.3f} · name similarity {r['name_similarity']} · brand {r['brand_score']}"
                )
                a, b, _ = st.columns([1, 1, 4])
                if a.button("Approve", key=f"ok{r['id']}", disabled=not REVIEW_WRITES):
                    import psycopg

                    from rpi.config import get_settings

                    with psycopg.connect(
                        get_settings().database_url, row_factory=psycopg.rows.dict_row
                    ) as conn:
                        approve_review(conn, r["id"], "dashboard")
                        conn.commit()
                    st.rerun()
                if b.button("Reject", key=f"no{r['id']}", disabled=not REVIEW_WRITES):
                    import psycopg

                    from rpi.config import get_settings

                    with psycopg.connect(
                        get_settings().database_url, row_factory=psycopg.rows.dict_row
                    ) as conn:
                        reject_review(conn, r["id"], "dashboard")
                        conn.commit()
                    st.rerun()
    footer()


def page_quality():
    st.title("Data quality and pipeline health")
    runs = d.runs()
    if runs.height:
        last = runs.row(0, named=True)
        c1, c2, c3 = st.columns(3)
        c1.metric("Last run", last["status"])
        c2.metric("Duration", f"{last['seconds']} s" if last["seconds"] is not None else "-")
        dq = d.dq_latest()
        c3.metric("Checks failing", f"{dq.filter(~pl.col('passed')).height} of {dq.height}")
        st.subheader("Row lineage")
        st.caption(
            "Every raw row is accounted for: it becomes an observation or a rejected record."
        )
        lin = d.lineage()
        st.dataframe(lin, hide_index=True, use_container_width=True)
        st.subheader("Checks from the latest run")
        st.dataframe(
            dq.select(
                "layer",
                "check_name",
                "scope",
                "severity",
                "passed",
                "observed",
                "threshold",
                "detail",
            ),
            hide_index=True,
            use_container_width=True,
        )
        st.subheader("Run history")
        st.dataframe(runs, hide_index=True, use_container_width=True)
    al = d.alerts()
    st.subheader("Alerts")
    if al.height:
        st.dataframe(al, hide_index=True, use_container_width=True)
    else:
        st.success("No alerts.")
    b = d.batch_status()
    if b.height:
        st.caption(
            "Bronze batches by status: "
            + ", ".join(f"{r['status']} {r['batches']}" for r in b.iter_rows(named=True))
        )
    footer()


st.session_state["zones"] = sidebar()
nav = st.navigation(
    [
        st.Page(page_overview, title="Overview", url_path="overview", default=True),
        st.Page(page_compare, title="Price comparison", url_path="compare"),
        st.Page(page_history, title="Price history", url_path="history"),
        st.Page(page_competitiveness, title="Competitiveness", url_path="competitiveness"),
        st.Page(page_promotions, title="Promotions", url_path="promotions"),
        st.Page(page_trends, title="Trends & price index", url_path="trends"),
        st.Page(page_basket, title="Cheapest basket", url_path="basket"),
        st.Page(page_review, title="Match review", url_path="review"),
        st.Page(page_quality, title="Data quality", url_path="quality"),
    ]
)
nav.run()
