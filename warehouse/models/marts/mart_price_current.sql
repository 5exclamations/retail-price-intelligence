-- Latest observation per product and price point, with the time it was observed.
-- Every figure shown to a user carries observed_at: no number without its timestamp.
with ranked as (
    select
        f.*, b.product_key,
        row_number() over (partition by f.store_item_id, f.price_point_key order by f.observed_date desc) as rn
    from {{ ref('fct_price_daily') }} f
    join {{ ref('dim_store_item') }} b on b.store_item_id = f.store_item_id
),
last_day as (select max(observed_date) as d from {{ ref('fct_price_daily') }})
select
    r.product_key,
    r.store_item_id,
    r.retailer_code,
    r.price_point_key,
    r.observed_date,
    r.observed_at,
    (l.d - r.observed_date) as days_since_observed,
    r.price_qepik,
    r.old_price_qepik,
    r.regular_price_qepik,
    r.is_promo,
    r.available
from ranked r, last_day l
where r.rn = 1
