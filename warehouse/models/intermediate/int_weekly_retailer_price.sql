-- Same as int_weekly_price_point but collapsed to the retailer, so a zoned chain with four zones
-- counts once (not four times) in market statistics and price indices.
select
    w.product_key,
    p.category,
    w.retailer_code,
    w.week_start,
    avg(w.regular_price_avg) as regular_price_avg,
    avg(w.effective_price_avg) as effective_price_avg,
    avg(w.promo_share) as promo_share
from {{ ref('int_weekly_price_point') }} w
join {{ ref('dim_product') }} p on p.product_key = w.product_key
group by 1, 2, 3, 4
