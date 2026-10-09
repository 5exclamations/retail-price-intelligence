with bounds as (
    select min(observed_date) as first_day, max(observed_date) as last_day
    from {{ source('silver', 'price_observation') }}
)
select
    to_char(d, 'YYYYMMDD')::int as date_key,
    d::date as date_day,
    date_trunc('week', d)::date as week_start,
    date_trunc('month', d)::date as month_start,
    extract(isodow from d)::int as iso_weekday,
    to_char(d, 'Dy') as weekday_name,
    extract(isodow from d) in (6, 7) as is_weekend
from bounds, generate_series(bounds.first_day, bounds.last_day, interval '1 day') as d
