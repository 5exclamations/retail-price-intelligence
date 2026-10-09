select
    d.week_start,
    a.retailer_code,
    count(*) as promo_price_days,
    (percentile_cont(0.5) within group (order by a.claimed_discount_bp))::int as median_claimed_bp,
    (percentile_cont(0.5) within group (order by a.real_discount_bp) filter (where a.ref_reliable))::int as median_real_bp,
    round((100.0 * avg(a.inflated_flag::int) filter (where a.ref_reliable))::numeric, 1) as inflated_share_pct,
    round((100.0 * avg(a.fake_flag::int) filter (where a.ref_reliable))::numeric, 1) as fake_share_pct
from {{ ref('mart_promo_analysis') }} a
join {{ ref('dim_date') }} d on d.date_day = a.observed_date
group by 1, 2
