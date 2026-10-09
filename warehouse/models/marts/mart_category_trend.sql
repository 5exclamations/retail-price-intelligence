-- Weekly category view: price indices plus how promotional the category is.
with promo as (
    select d.week_start, a.category,
           round((100.0 * count(*)::numeric / nullif(max(w.price_days), 0)), 1) as promo_share_pct,
           (percentile_cont(0.5) within group (order by a.real_discount_bp) filter (where a.ref_reliable))::int as median_real_bp
    from {{ ref('mart_promo_analysis') }} a
    join {{ ref('dim_date') }} d on d.date_day = a.observed_date
    join (
        select d2.week_start, p.category, count(*) as price_days
        from {{ ref('fct_price_daily') }} f
        join {{ ref('dim_store_item') }} b on b.store_item_id = f.store_item_id
        join {{ ref('dim_product') }} p on p.product_key = b.product_key
        join {{ ref('dim_date') }} d2 on d2.date_key = f.date_key
        group by 1, 2
    ) w on w.week_start = d.week_start and w.category = a.category
    group by 1, 2
)
select i.week_start, i.category, i.index_regular, i.index_effective, i.wow_change_pct, i.n_items,
       pr.promo_share_pct, pr.median_real_bp
from {{ ref('mart_price_index') }} i
left join promo pr on pr.week_start = i.week_start and pr.category = i.category
