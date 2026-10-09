-- Every promotional price-day, judged against the market rather than against the retailer's own tag.
--   claimed_discount_bp  (old - price) / old                       what the shelf tag says
--   real_discount_bp     (market ref - price) / market ref          market ref = median ordinary price at other retailers that day
--   inflated_flag        claimed exceeds real by > inflated_gap_bp  (needs a reliable market reference)
--   fake_flag            price is not below the market reference    no real saving at all
-- Discounts are integer basis points (100 bp = 1%).
with promo as (
    select f.observed_date, f.store_item_id, f.price_point_key, f.retailer_code, f.price_qepik,
           f.old_price_qepik, f.regular_price_qepik, f.regular_price_source, f.observed_at, f.available,
           b.product_key, p.category
    from {{ ref('fct_price_daily') }} f
    join {{ ref('dim_store_item') }} b on b.store_item_id = f.store_item_id
    join {{ ref('dim_product') }} p on p.product_key = b.product_key
    where f.is_promo
),
with_ref as (
    select pr.*, m.market_ref, m.n_ref
    from promo pr
    left join lateral (
        select (percentile_cont(0.5) within group (order by r.regular_price_qepik))::int as market_ref, count(*) as n_ref
        from {{ ref('int_retailer_regular_daily') }} r
        where r.observed_date = pr.observed_date and r.product_key = pr.product_key
          and r.retailer_code <> pr.retailer_code
    ) m on true
),
judged as (
    select
        *,
        round((old_price_qepik - price_qepik) * 10000.0 / old_price_qepik)::int as claimed_discount_bp,
        case when market_ref is not null
             then round((market_ref - price_qepik) * 10000.0 / market_ref)::int end as real_discount_bp,
        round((old_price_qepik - regular_price_qepik) * 10000.0 / nullif(regular_price_qepik, 0))::int as claim_vs_own_regular_bp,
        (market_ref is not null and market_ref between old_price_qepik * 0.33 and old_price_qepik * 3.0) as ref_reliable
    from with_ref
)
select
    observed_date, product_key, store_item_id, price_point_key, retailer_code, category,
    price_qepik, old_price_qepik, regular_price_qepik, regular_price_source, market_ref as market_ref_qepik,
    n_ref as market_ref_retailers, claimed_discount_bp, real_discount_bp, claim_vs_own_regular_bp,
    ref_reliable,
    (ref_reliable and claimed_discount_bp - real_discount_bp > {{ var('inflated_gap_bp') }}) as inflated_flag,
    (ref_reliable and price_qepik >= market_ref) as fake_flag,
    available, observed_at
from judged
