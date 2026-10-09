-- A promotion by definition has an old price above the current price.
select * from {{ ref('fct_price_daily') }}
where is_promo and old_price_qepik <= price_qepik
