-- Fixed-base matched-model Jevons price index. Methodology: docs/METHODOLOGY.md.
--   * unit: one product sold by one retailer (zones averaged), weekly average price in qepik
--   * base: first complete week; only (product, retailer) pairs priced in the base week take part
--   * index_t = 100 * exp( mean_over_pairs( ln( p_t / p_base ) ) )   (geometric mean, equal weights)
--   * two variants: regular price (promotions removed) and effective price (what the shelf shows)
with weekly as (select * from {{ ref('int_weekly_retailer_price') }}),
base_week as (select min(week_start) as week_start from weekly),
base as (
    select w.product_key, w.retailer_code, w.regular_price_avg as base_regular, w.effective_price_avg as base_effective
    from weekly w join base_week b on b.week_start = w.week_start
),
relatives as (
    select
        w.week_start, w.category,
        ln(w.regular_price_avg / b.base_regular) as ln_regular,
        ln(w.effective_price_avg / b.base_effective) as ln_effective
    from weekly w join base b using (product_key, retailer_code)
),
rolled as (
    select
        week_start,
        coalesce(category, 'All categories') as category,
        round((100 * exp(avg(ln_regular)))::numeric, 2) as index_regular,
        round((100 * exp(avg(ln_effective)))::numeric, 2) as index_effective,
        count(*) as n_items
    from relatives
    group by grouping sets ((week_start, category), (week_start))
)
select
    r.*,
    round((100 * (r.index_regular / nullif(lag(r.index_regular) over (partition by r.category order by r.week_start), 0) - 1))::numeric, 2)
        as wow_change_pct
from rolled r
