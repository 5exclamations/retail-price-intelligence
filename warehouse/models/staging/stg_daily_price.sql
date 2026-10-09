-- Collapse the stores of one zone into a single price per item and day.
-- mode() takes the price most stores agree on, so one lagging store cannot move the zone price;
-- n_distinct_prices records how often that disagreement happens (see the zone consistency check).
select
    store_item_id,
    retailer_code,
    price_point_key,
    price_zone,
    observed_date,
    (mode() within group (order by price_qepik))::int as price_qepik,
    (mode() within group (order by old_price_qepik))::int as old_price_qepik,
    max(observed_at) as observed_at,
    bool_or(available) as available,
    count(*) as n_stores,
    count(distinct price_qepik) as n_distinct_prices
from {{ ref('stg_price_observation') }}
group by store_item_id, retailer_code, price_point_key, price_zone, observed_date
