-- Adds the *regular* price to every daily price.
--
-- A retailer's "was" price is a claim, not a fact (about 5% of promotions in the source data inflate it).
-- The regular price used for indices and comparisons is therefore the last price this item was
-- actually seen at without a promotion marker. Only when a promotion starts before any ordinary
-- price was observed do we fall back to the claimed old price, and that is flagged.
with flagged as (
    select
        *,
        (old_price_qepik is not null and old_price_qepik > price_qepik) as is_promo
    from {{ ref('stg_daily_price') }}
),
grouped as (
    select
        *,
        -- Increments on every non-promo day, so a promo run shares its group with the ordinary day before it.
        sum(case when is_promo then 0 else 1 end)
            over (partition by store_item_id, price_point_key order by observed_date) as grp
    from flagged
),
last_ordinary as (
    select
        *,
        max(case when not is_promo then price_qepik end)
            over (partition by store_item_id, price_point_key, grp) as last_ordinary_price
    from grouped
)
select
    store_item_id, retailer_code, price_point_key, price_zone, observed_date, observed_at,
    price_qepik, old_price_qepik, available, n_stores, n_distinct_prices, is_promo,
    case when not is_promo then price_qepik
         else coalesce(last_ordinary_price, old_price_qepik) end as regular_price_qepik,
    case when is_promo and last_ordinary_price is null then 'claimed' else 'observed' end as regular_price_source
from last_ordinary
