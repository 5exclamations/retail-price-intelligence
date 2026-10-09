-- By construction the first week of every series is the base: the index must be exactly 100.
select * from {{ ref('mart_price_index') }}
where week_start = (select min(week_start) from {{ ref('mart_price_index') }})
  and (index_regular <> 100 or index_effective <> 100)
