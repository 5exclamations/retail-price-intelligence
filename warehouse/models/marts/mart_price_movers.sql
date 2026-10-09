-- Biggest regular-price moves between the latest day and 28 days earlier, per product and retailer.
with last_day as (select max(observed_date) as d from {{ ref('fct_price_daily') }}),
at_date as (
    select b.product_key, f.retailer_code, f.observed_date,
           (percentile_cont(0.5) within group (order by f.regular_price_qepik))::int as regular_price_qepik
    from {{ ref('fct_price_daily') }} f
    join {{ ref('dim_store_item') }} b on b.store_item_id = f.store_item_id
    group by 1, 2, 3
)
select
    n.product_key, p.product_name, p.category, n.retailer_code,
    o.regular_price_qepik as price_28d_ago_qepik,
    n.regular_price_qepik as price_now_qepik,
    round((n.regular_price_qepik - o.regular_price_qepik) * 10000.0 / o.regular_price_qepik)::int as change_bp,
    n.observed_date as observed_date
from at_date n
join last_day l on n.observed_date = l.d
join at_date o on o.product_key = n.product_key and o.retailer_code = n.retailer_code and o.observed_date = l.d - 28
join {{ ref('dim_product') }} p on p.product_key = n.product_key
where o.regular_price_qepik > 0
