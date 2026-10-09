-- One row per silver observation, with the price point resolved.
-- A price point is (retailer, price zone). Single-price chains have zone 'all'; zoned chains have one
-- price point per measured zone, so a zoned retailer's price is never shown without its zone.
select
    o.id as observation_id,
    o.store_item_id,
    i.retailer_code,
    coalesce(s.price_zone, 'all') as price_zone,
    i.retailer_code || ':' || coalesce(s.price_zone, 'all') as price_point_key,
    o.observed_date,
    o.observed_at,
    o.price_qepik,
    o.old_price_qepik,
    o.available,
    o.promo_until
from {{ source('silver', 'price_observation') }} o
join {{ source('silver', 'store_item') }} i on i.id = o.store_item_id
left join {{ source('silver', 'store') }} s on s.id = o.store_id
