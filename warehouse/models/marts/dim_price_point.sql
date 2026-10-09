-- A price point is what a shopper actually faces: a single-price chain, or one price zone of a zoned chain.
select distinct
    p.price_point_key,
    p.retailer_code,
    r.name as retailer_name,
    p.price_zone,
    p.price_zone <> 'all' as requires_zone_choice,
    case when p.price_zone = 'all' then r.name else r.name || ' (zone ' || p.price_zone || ')' end as label
from {{ ref('stg_daily_price') }} p
join {{ source('silver', 'retailer') }} r on r.code = p.retailer_code
