-- Weekly competitiveness of every price point against the market, on products sold by >= 3 retailers.
--   price_index_vs_market  100 = market median; geometric mean of (price point price / market median)
--   share_cheapest_pct     share of compared products where this price point is at or below every other retailer
with pp as (select * from {{ ref('int_weekly_price_point') }}),
retailer_level as (
    select product_key, retailer_code, week_start,
           percentile_cont(0.5) within group (order by regular_price_avg) as price
    from pp group by 1, 2, 3
),
market as (
    select product_key, week_start, percentile_cont(0.5) within group (order by price) as market_median
    from retailer_level group by 1, 2 having count(*) >= 3
),
compared as (
    select
        pp.week_start, pp.price_point_key, pp.retailer_code, pp.promo_share,
        ln(pp.regular_price_avg / m.market_median) as ln_rel,
        pp.regular_price_avg <= coalesce((
            select min(rl.price) from retailer_level rl
            where rl.product_key = pp.product_key and rl.week_start = pp.week_start
              and rl.retailer_code <> pp.retailer_code), 1e12) as is_cheapest
    from pp join market m using (product_key, week_start)
)
select
    week_start, price_point_key, retailer_code,
    count(*) as n_products,
    round((100 * exp(avg(ln_rel)))::numeric, 2) as price_index_vs_market,
    round((100.0 * avg(is_cheapest::int))::numeric, 1) as share_cheapest_pct,
    round((100.0 * avg(promo_share))::numeric, 1) as promo_share_pct
from compared
group by 1, 2, 3
