select * from {{ ref('fct_price_daily') }}
where observed_at > now() + interval '1 day'
