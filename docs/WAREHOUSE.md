# Warehouse (dbt) reference

The gold layer is a dbt project in `warehouse/`. Silver tables are declared as sources (`models/staging/sources.yml`); staging models are views, intermediate and mart models are tables, and `fct_price_daily` is incremental.

## Lineage

```mermaid
flowchart LR
    SILVER[(silver tables)]
    basket_definition[basket_definition]:::dim
    basket_quantity[basket_quantity]:::seed
    dim_category[dim_category]:::dim
    dim_date[dim_date]:::dim
    dim_price_point[dim_price_point]:::dim
    dim_product[dim_product]:::dim
    dim_retailer[dim_retailer]:::dim
    dim_store[dim_store]:::dim
    dim_store_item[dim_store_item]:::dim
    fct_price_daily[fct_price_daily]:::fct
    int_daily_price_regular[int_daily_price_regular]:::stg
    int_retailer_regular_daily[int_retailer_regular_daily]:::int
    int_weekly_price_point[int_weekly_price_point]:::int
    int_weekly_retailer_price[int_weekly_retailer_price]:::int
    mart_category_trend[mart_category_trend]:::mart
    mart_cheapest_basket[mart_cheapest_basket]:::mart
    mart_price_changes_daily[mart_price_changes_daily]:::mart
    mart_price_current[mart_price_current]:::mart
    mart_price_index[mart_price_index]:::mart
    mart_price_movers[mart_price_movers]:::mart
    mart_promo_analysis[mart_promo_analysis]:::mart
    mart_promo_retailer_summary[mart_promo_retailer_summary]:::mart
    mart_retailer_competitiveness[mart_retailer_competitiveness]:::mart
    snap_product_match[snap_product_match]:::snap
    snap_store_zone[snap_store_zone]:::snap
    stg_daily_price[stg_daily_price]:::stg
    stg_price_observation[stg_price_observation]:::stg
    basket_definition --> mart_cheapest_basket
    basket_quantity --> basket_definition
    dim_date --> int_weekly_price_point
    dim_date --> mart_category_trend
    dim_date --> mart_promo_retailer_summary
    dim_product --> basket_definition
    dim_product --> dim_category
    dim_product --> dim_store_item
    dim_product --> int_weekly_retailer_price
    dim_product --> mart_category_trend
    dim_product --> mart_price_changes_daily
    dim_product --> mart_price_movers
    dim_product --> mart_promo_analysis
    dim_retailer --> basket_definition
    dim_store_item --> int_retailer_regular_daily
    dim_store_item --> int_weekly_price_point
    dim_store_item --> mart_category_trend
    dim_store_item --> mart_cheapest_basket
    dim_store_item --> mart_price_changes_daily
    dim_store_item --> mart_price_current
    dim_store_item --> mart_price_movers
    dim_store_item --> mart_promo_analysis
    fct_price_daily --> int_retailer_regular_daily
    fct_price_daily --> int_weekly_price_point
    fct_price_daily --> mart_category_trend
    fct_price_daily --> mart_cheapest_basket
    fct_price_daily --> mart_price_changes_daily
    fct_price_daily --> mart_price_current
    fct_price_daily --> mart_price_movers
    fct_price_daily --> mart_promo_analysis
    int_daily_price_regular --> fct_price_daily
    int_retailer_regular_daily --> mart_promo_analysis
    int_weekly_price_point --> int_weekly_retailer_price
    int_weekly_price_point --> mart_retailer_competitiveness
    int_weekly_retailer_price --> mart_price_index
    mart_price_index --> mart_category_trend
    mart_promo_analysis --> mart_category_trend
    mart_promo_analysis --> mart_promo_retailer_summary
    SILVER --> dim_date
    SILVER --> stg_price_observation
    SILVER --> dim_product
    SILVER --> dim_store_item
    SILVER --> snap_product_match
    SILVER --> dim_price_point
    SILVER --> dim_retailer
    SILVER --> dim_store
    SILVER --> snap_store_zone
    stg_daily_price --> dim_price_point
    stg_daily_price --> int_daily_price_regular
    stg_price_observation --> stg_daily_price
    classDef stg fill:#e8f1fb,stroke:#6a9fd8
    classDef int fill:#eef6e8,stroke:#7fb069
    classDef dim fill:#fff4d6,stroke:#d9a400
    classDef fct fill:#fde2e0,stroke:#d9534f
    classDef mart fill:#efe3f6,stroke:#9b59b6
    classDef seed fill:#eee,stroke:#999
    classDef snap fill:#e6f4f1,stroke:#2a9d8f
```

## Models

| Model | Kind | Grain | Purpose |
|---|---|---|---|
| `stg_price_observation` | view | observation | resolves each observation to a price point (`retailer:zone`) |
| `stg_daily_price` | view | item x price point x day | collapses the stores of a zone with `mode()` |
| `int_daily_price_regular` | view | item x price point x day | adds the regular price (last ordinary price observed) |
| `fct_price_daily` | incremental | item x price point x day | the fact: integer qepik prices, promo flag, observation time |
| `dim_date`, `dim_retailer`, `dim_price_point`, `dim_store`, `dim_category` | table | one row per member | dimensions; `dim_price_point.requires_zone_choice` carries the zone rule |
| `dim_product` | table | canonical product | published products only (quarantined excluded) |
| `dim_store_item` | table | retailer SKU | bridge SKU to product; keeps facts independent of re-matching |
| `mart_price_current` | table | product x price point | latest observation with its time |
| `mart_price_index` | table | week x category | fixed-base matched-model Jevons index |
| `mart_retailer_competitiveness` | table | week x price point | price index vs market and share cheapest |
| `mart_promo_analysis` | table | promotion price-day | claimed versus market-based discount, inflated and fake flags |
| `mart_promo_retailer_summary`, `mart_category_trend` | table | week x retailer / category | rollups |
| `mart_price_changes_daily`, `mart_price_movers` | table | day x retailer x category / product x retailer | price moves |
| `basket_definition`, `mart_cheapest_basket` | table | product / day x price point | default basket and its daily cost |
| `snap_product_match`, `snap_store_zone` | snapshot | SKU / store | SCD2 history of match decisions and measured zones |

Formulas: [`METHODOLOGY.md`](METHODOLOGY.md). Tests: 62 (see `warehouse/models/marts/schema.yml` and `warehouse/tests/`). Generate the browsable docs site with `make dbt-docs`.
