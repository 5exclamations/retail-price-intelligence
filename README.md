# Retail Price Intelligence Platform

[![CI](https://github.com/5exclamations/retail-price-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/5exclamations/retail-price-intelligence/actions/workflows/ci.yml)
[![Security](https://github.com/5exclamations/retail-price-intelligence/actions/workflows/security.yml/badge.svg)](https://github.com/5exclamations/retail-price-intelligence/actions/workflows/security.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![PostgreSQL](https://img.shields.io/badge/postgres-16-336791)
![dbt](https://img.shields.io/badge/dbt-core-FF694B)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A data platform that answers the questions a Baku shopper, a category manager and a pricing analyst all ask: *where is this cheapest today, which discounts are real, and how fast are prices rising?*

It ingests supermarket price feeds that share no identifier scheme, currency format or notion of "price", cleans them, matches the same product across chains, and publishes analytics-ready tables, a REST API and a dashboard. Everything runs locally on **synthetic data**: no retailer access is needed, no scraping code exists, and no real retailer data is included.

**Built to show:** layered ELT (bronze / silver / gold), incremental and idempotent pipelines, dimensional modelling with dbt, data-quality engineering, entity resolution with a human-in-the-loop queue, and a tested FastAPI service, all reproducible from one command.

```text
synthetic feeds ─► bronze ─► silver ─► product matching ─► gold (dbt) ─► REST API
 (5 formats)       raw       clean      confidence +        marts,         dashboard
                  + lineage  + valid.   review queue        index, tests
                                  Prefect · Postgres · Polars · dbt · FastAPI · Streamlit
```

![Overview](docs/screenshots/01-overview.png)

## Contents

1. [Business problem](#business-problem)
2. [What it does](#what-it-does)
3. [Architecture](#architecture)
4. [Data pipeline](#data-pipeline)
5. [Database schema](#database-schema)
6. [Technology choices](#technology-choices)
7. [Screenshots](#screenshots)
8. [Sample SQL](#sample-sql)
9. [Data quality methodology](#data-quality-methodology)
10. [Local setup](#local-setup)
11. [Demo scenarios](#demo-scenarios)
12. [Verified results](#verified-results)
13. [Technical trade-offs](#technical-trade-offs)
14. [Interview talking points](#interview-talking-points)
15. [Data sources and licensing](#data-sources-and-licensing)
16. [Repository layout](#repository-layout)

## Quick start

```bash
git clone https://github.com/5exclamations/retail-price-intelligence.git && cd retail-price-intelligence
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
docker compose up -d postgres          # or any Postgres 16, see Local setup
make demo                              # migrate, generate 90 days of feeds, run the pipeline (~2 min)
make api                               # http://localhost:8000/docs
make dashboard                         # http://localhost:8501
```

Or the whole stack in containers: `docker compose up --build`.

## Business problem

Grocery prices in Azerbaijan are hard to compare for four reasons the platform is built around.

* **No shared product identity.** Chains name the same milk `Xəzər Süd 3.2% 1 l`, `XƏZƏR SÜD 3.2% 1L` and `Süd Xəzər 3.2% 1l`. Barcodes are present on some rows, missing on others, sometimes wrong. Weighed goods (tomatoes, chicken) have no barcode at all.
* **A chain is not one price list.** Some chains charge the same everywhere; others price by zone, and zones do not follow store format. A price shown without choosing a store or zone is a guess.
* **Discounts are partly theatre.** In the synthetic data about 5% of promotions are generated with an inflated "was" price. Judging a promotion against the shelf tag misleads; judging it against what other chains charge for the same product does not.
* **Feeds break.** Duplicated lines, zero prices, truncated files, a feed that suddenly reports qepik instead of AZN. A pipeline that loads these quietly produces confident, wrong analytics.

## What it does

| Capability | Where |
|---|---|
| Incremental, idempotent ingestion of five differently shaped feeds (JSONL, JSON array, CSV) | `rpi/ingest`, `rpi/silver` |
| Validation, deduplication, exact money handling (integer qepik, never float) | `rpi/silver` |
| Cross-retailer product matching with confidence scores, a review queue and quarantine | `rpi/matching` |
| Dimensional model, marts and 62 dbt tests | `warehouse/` |
| Price comparison, history, competitiveness, promotion honesty, category trends, price index, cheapest basket | `warehouse/models/marts`, `rpi/dashboard` |
| REST API (19 routes, OpenAPI docs) | `rpi/api` |
| Prefect orchestration with retries, run ledger, alerts and failure reports | `rpi/flows` |
| Data-quality checks (29 per run on the demo data, plus the dbt result) and batch-level gates | `rpi/dq`, `rpi/silver/transform.py` |
| Deterministic synthetic data with injected defects and ground truth | `rpi/synth` |
| 123 tests against a real Postgres, CI and security workflows, Docker Compose | `rpi/tests`, `.github/workflows/`, `docker-compose.yml` |

Domain rules enforced in code and tests:

* Money is an `int` of **qepik** (1 AZN = 100 qepik) everywhere; discounts are integer basis points. Formatting happens only at the display boundary.
* Price history is **append-only** (a database trigger rejects `UPDATE` and `DELETE`).
* A zoned chain is **never priced without a zone**; the API lists the unpriced zones under `excluded_price_points`.
* Quarantined (doubtful) merges **never reach gold**, the API or the dashboard.
* Every number shown to a user comes with the **time it was observed**.
* A promotion's real discount is measured against the **median ordinary price at other chains**, not the retailer's own tag.
* Weighed goods are compared per kilogram only, and never merge with packaged goods.

## Architecture

```mermaid
flowchart LR
    subgraph Sources["Sources (synthetic)"]
        F1[baku_fresh<br/>JSONL]
        F2[caspianmart<br/>JSONL]
        F3[absheron<br/>JSON, 4 price zones]
        F4[shirvan<br/>CSV]
        F5[sumqayit<br/>JSONL]
    end
    LAND[(landing files<br/>data/landing)]
    subgraph PG["PostgreSQL 16"]
        direction TB
        B[(bronze<br/>raw payload + lineage)]
        S[(silver<br/>clean, validated,<br/>matched)]
        G[(gold<br/>dims, facts, marts)]
        O[(ops<br/>runs, checks, alerts)]
    end
    PF{{Prefect flow<br/>retries, run ledger}}
    DBT[dbt Core<br/>models + tests]
    API[FastAPI<br/>analytics REST API]
    UI[Streamlit<br/>dashboard]
    Sources --> LAND --> PF
    PF -->|ingest| B
    PF -->|Polars: parse, validate, dedupe| S
    B --> S
    PF -->|match| S
    PF -->|dq checks| O
    PF --> DBT
    S --> DBT --> G
    G --> API
    G --> UI
    O --> API
    O --> UI
    S -->|review queue| API
```

**Layers and contracts**

| Layer | Schema | Holds | Contract |
|---|---|---|---|
| Bronze | `bronze` | Every line of every file as `jsonb`, with source, file hash, line number | Nothing is cleaned or dropped. Same bytes are never loaded twice. |
| Silver | `silver` | Standardised items, append-only price observations, rejected rows, canonical products, matches | Every bronze row becomes an observation or a rejected record. Money is integer qepik. |
| Gold | `gold`, `staging` | Dimensions, daily fact, marts, snapshots | Built by dbt from silver only; quarantined products excluded; tested. |
| Ops | `ops` | Pipeline runs, steps, data-quality results, alerts, migrations | Queryable run history without a Prefect server. |

## Data pipeline

```mermaid
flowchart TD
    A[Landing files<br/>source_YYYY-MM-DD.ext] --> B{sha256 already<br/>ingested?}
    B -- yes --> B1[skip: idempotent]
    B -- no --> C[Bronze: one row per line,<br/>broken lines kept as _unparseable]
    C --> D[Parse with the source's parser<br/>decimal strings / float AZN / int qepik -> int qepik]
    D --> E[Row validation<br/>missing sku or name, price <= 0, unknown store, bad JSON]
    E -->|invalid| R[(silver.rejected_record<br/>with reason)]
    E --> F[Deduplicate on sku + store]
    F -->|duplicate| R
    F --> G{Batch gates}
    G -->|"unknown source<br/>> 20% invalid<br/>median price outside 0.30-50 AZN<br/>rows < 50% of trailing median"| X[Batch rejected<br/>alert + bronze status]
    G -->|pass| H[Enrich items: units, brand, EAN kind, category]
    H --> I[Append observations<br/>ON CONFLICT DO NOTHING]
    I --> J[Match products<br/>EAN, fingerprint, fuzzy]
    J -->|uncertain| Q[(match_review queue)]
    J -->|doubtful merge| QU[Quarantine]
    J --> K[Data-quality checks -> ops.dq_result]
    K --> L[dbt snapshot + build: dims, fact, marts, 62 tests]
    L --> M[API and dashboard]
    X -.-> K
```

Each arrow is a Prefect task with retries; every step also writes a row to `ops.step_run`. If a step raises, the run is marked `failed`, an alert is raised and `data/reports/failure_<run_id>.json` is written with the traceback and the steps completed so far. Rejected batches or error-level checks mark the run `degraded` without stopping it, so one bad feed does not block the other four.

## Database schema

```mermaid
erDiagram
    ingest_batch ||--o{ raw_record : contains
    ingest_batch ||--o{ price_observation : "loaded from"
    ingest_batch ||--o{ rejected_record : rejects
    retailer ||--o{ store : has
    retailer ||--o{ store_item : sells
    store_item ||--o{ price_observation : "priced by"
    store ||--o{ price_observation : "quoted at"
    store_item ||--|| product_match : "matched as"
    product ||--o{ product_match : groups
    store_item ||--o{ match_review : "needs review"
    product ||--o{ match_review : candidate

    ingest_batch {
        bigint batch_id PK
        text source
        text file_sha256 "unique per source"
        date business_date
        text status
    }
    raw_record {
        bigint batch_id PK
        int row_num PK
        jsonb payload
        text record_hash
    }
    retailer {
        text code PK
        text price_model "single or zoned"
    }
    store {
        bigint id PK
        text store_code
        text price_zone "measured, not inferred"
    }
    store_item {
        bigint id PK
        text sku
        text name_norm
        text brand
        numeric unit_value
        text unit_type
        text ean_kind
        text category
    }
    price_observation {
        bigint id PK
        int price_qepik "integer money"
        int old_price_qepik
        date observed_date
        timestamptz observed_at "append-only"
    }
    product {
        bigint id PK
        text name
        text category
        boolean quarantined
        text quarantine_reason
    }
    product_match {
        bigint store_item_id PK
        bigint product_id FK
        text method
        numeric confidence
        text status
    }
    match_review {
        bigint id PK
        numeric score
        jsonb features
        text status
    }
    rejected_record {
        bigint id PK
        int row_num
        text reason
    }
```

Gold (built by dbt):

```mermaid
erDiagram
    dim_product ||--o{ dim_store_item : "canonical product of"
    dim_store_item ||--o{ fct_price_daily : "priced daily"
    dim_price_point ||--o{ fct_price_daily : at
    dim_retailer ||--o{ dim_price_point : "split by zone"
    dim_date ||--o{ fct_price_daily : on
    dim_category ||--o{ dim_product : classifies
    fct_price_daily ||--o{ mart_promo_analysis : "promotion days"
    fct_price_daily ||--o{ mart_price_current : "latest row"
    fct_price_daily ||--o{ mart_price_index : "weekly Jevons"
    fct_price_daily ||--o{ mart_retailer_competitiveness : "weekly vs market"
    fct_price_daily ||--o{ mart_cheapest_basket : "daily basket cost"

    fct_price_daily {
        int date_key FK
        int store_item_id FK
        text price_point_key FK
        int price_qepik
        int old_price_qepik
        int regular_price_qepik
        boolean is_promo
        timestamptz observed_at
    }
    dim_price_point {
        text price_point_key PK
        text retailer_code FK
        text price_zone
        boolean requires_zone_choice
    }
    dim_product {
        bigint product_key PK
        text product_name
        text category
        text unit_type
        int retailer_count
    }
    dim_store_item {
        bigint store_item_id PK
        bigint product_key FK
        text match_method
        numeric match_confidence
    }
```

Grain of `fct_price_daily`: one row per **store item × price point × day**. A *price point* is a single-price chain, or one measured zone of a zoned chain (`absheron:B`). Facts are keyed by SKU, not by product, so approving a review re-labels the SKU's whole history on the next build without rewriting the fact table. Full column documentation lives in `warehouse/models/**/schema.yml`; the model catalogue and lineage graph are in [`docs/WAREHOUSE.md`](docs/WAREHOUSE.md).

## Technology choices

| Choice | Used for | Why this and not the alternative |
|---|---|---|
| **PostgreSQL 16** | Bronze, silver, gold, ops | One engine for OLTP-style writes (append-only history, review decisions) and analytical marts at this scale (0.25M rows). Triggers, partial unique indexes on `COALESCE(store_id, 0)` and `percentile_cont` are used directly. A columnar warehouse would only pay off at 100x the volume. |
| **Polars** | Silver: parsing, validation, dedupe | Typed expressions make validation rules readable and fast; the same code handles one file or a year of backfill. Pandas would work; Polars avoids silent dtype coercion on nullable columns. |
| **dbt Core** | Gold layer, tests, snapshots | SQL transformations that are versioned, tested and documented. Custom generic tests (`unique_combination`, `integer_typed`, zoned-price-point rule) avoid a package download. |
| **Prefect 3** | Orchestration | Retries, task states and a failure hook with plain Python functions; runs in-process with no server to operate. Step logic stays in `rpi/flows/steps.py` so it is testable without Prefect. |
| **FastAPI + psycopg pool** | REST API | Typed parameters, generated OpenAPI, no ORM between SQL and the response. |
| **Streamlit + Plotly** | Dashboard | Fastest route to an analyst-grade UI over the gold tables; reads the same marts the API serves. |
| **RapidFuzz** | Fuzzy name similarity | Fast token-based ratios; blocking on size and brand keeps comparisons small. |
| **pytest + real Postgres** | Tests | Triggers, unique indexes, `percentile_cont` and dbt are Postgres behaviour; mocks would test none of it. |
| **Docker Compose, GitHub Actions** | Reproducibility, CI | One command to run the stack; lint, tests and an end-to-end pipeline run on every change. |

## Screenshots

All screenshots are taken from the running dashboard on the synthetic dataset (see [Verified results](#verified-results)).

| | |
|---|---|
| ![Price comparison](docs/screenshots/02-price-comparison.png) **Price comparison.** Cheapest first, promotions hatched, each price with its observation time. | ![Price history](docs/screenshots/03-price-history.png) **Price history.** Shelf price with promotion days marked, plus the regular price with promotions removed. |
| ![Competitiveness](docs/screenshots/04-competitiveness.png) **Retailer competitiveness.** Weekly price index against the market median and share of products where the store is cheapest. | ![Promotions](docs/screenshots/05-promotions.png) **Promotion analysis.** Claimed discount against real (market-based) discount; points below the diagonal are overstated. |
| ![Trends](docs/screenshots/06-trends-price-index.png) **Category trends and price index.** Regular and effective variants, methodology inline. | ![Basket](docs/screenshots/07-cheapest-basket.png) **Cheapest basket.** Only stores that stock the whole basket are ranked; incomplete ones are listed separately. |
| ![Review queue](docs/screenshots/08-match-review.png) **Match review.** Uncertain merges with score and evidence; approve or reject. | ![Incident](docs/screenshots/10-data-quality-incident.png) **Incident.** A feed in the wrong unit was rejected; the run is `degraded` and the stale feed is visible. |

Data quality page in a healthy state: [`docs/screenshots/09-data-quality.png`](docs/screenshots/09-data-quality.png).

## Sample SQL

Nine documented queries live in [`docs/sample_queries.sql`](docs/sample_queries.sql); all were executed against the demo database. Three of them:

**Which chain is cheapest, honestly?** Price index against the market median, last week (100 = market). `zone D` of the zoned chain is cheapest on average; `zone C` is the dearest, which is why the zone must be chosen.

```sql
SELECT pp.label, c.n_products, c.price_index_vs_market, c.share_cheapest_pct
FROM gold.mart_retailer_competitiveness c
JOIN gold.dim_price_point pp USING (price_point_key)
WHERE c.week_start = (SELECT max(week_start) FROM gold.mart_retailer_competitiveness)
ORDER BY c.price_index_vs_market;
--            label           | n_products | price_index_vs_market | share_cheapest_pct
--  Absheron Markets (zone D) |        201 |                 98.09 |               71.1
--  Sumqayit Discount         |        150 |                 98.72 |               78.7
--  CaspianMart               |        204 |                 99.15 |               78.4
--  ...
--  Absheron Markets (zone C) |        201 |                107.08 |                1.0
```

**Which promotions are overstated?** Shelf tag against the market:

```sql
SELECT p.product_name, pp.label, a.price_qepik, a.old_price_qepik AS shelf_was,
       a.market_ref_qepik, a.claimed_discount_bp / 100.0 AS claimed_pct, a.real_discount_bp / 100.0 AS real_pct
FROM gold.mart_promo_analysis a
JOIN gold.dim_product p USING (product_key) JOIN gold.dim_price_point pp USING (price_point_key)
WHERE a.observed_date = (SELECT max(observed_date) FROM gold.mart_promo_analysis) AND a.inflated_flag
ORDER BY a.claimed_discount_bp - a.real_discount_bp DESC LIMIT 3;
--  Çinar Xama 20% 200 Qr  | Sumqayit Discount | 469 | 869 | 459 | 46.03 | -2.18
--  Aypara Biskvit 200 Qr  | Baku Fresh        | 449 | 739 | 436 | 39.24 | -2.98
--  Şəfəq Qəhvə 3 In 1 ... | Absheron (zone C) | 835 | 1240 | 789 | 32.66 | -5.83
```

The first row says: the shelf claims 46% off (8.69 → 4.69 AZN), but other chains sell the product at 4.59 AZN every day, so the real discount is negative.

**Does every raw row end up somewhere?**

```sql
SELECT b.source, sum(b.row_count) AS bronze_rows,
       (SELECT count(*) FROM silver.price_observation o JOIN silver.store_item i ON i.id = o.store_item_id
         WHERE i.retailer_code = b.source) AS observations,
       (SELECT count(*) FROM silver.rejected_record r WHERE r.source = b.source) AS rejected
FROM bronze.ingest_batch b WHERE b.kind = 'prices' AND b.status = 'silver_done' GROUP BY b.source;
--  absheron   | 169431 | 167060 | 2371      (169431 = 167060 + 2371)
--  baku_fresh |  22978 |  22665 |  313
```

## Data quality methodology

Quality is checked at four points, each catching a different class of damage. Details: [`docs/DATA_QUALITY.md`](docs/DATA_QUALITY.md).

| Point | Mechanism | Example it catches |
|---|---|---|
| **Row** (silver) | Per-row validation, dedupe, exact money conversion; every rejected row is stored with a reason | Zero or negative price, empty name, truncated JSON line, unknown store, exact and conflicting duplicates |
| **Batch** (silver gate) | Whole file refused before any row enters silver, alert raised | Feed in the wrong unit (median 389 AZN), more than 20% invalid rows, rows below half of the trailing median, unregistered source |
| **Run** (`rpi/dq`) | 29 checks per run on the demo data, stored in `ops.dq_result` with severity `warn` or `error` | Row reconciliation (bronze rows = observations + rejected), stale feeds, price-range, duplicate keys, zone price consistency, unmapped categories, review backlog, quarantine rate, implausible price jumps |
| **Model** (dbt) | 62 tests, 5 of them singular SQL tests | Fact grain, integer money columns, `relationships`, base-week index = 100, no quarantined product in gold, zoned chain always split by zone |

Two choices make the checks trustworthy rather than decorative:

1. **Defects are injected deterministically and counted.** The generator records how many zero prices, empty names, duplicates and broken lines it wrote. A test asserts silver rejected exactly those numbers, per reason, no more and no fewer.
2. **Marts are tested on hand-built data to the qepik.** `rpi/tests/test_gold_exact.py` builds four retailers with chosen prices and checks the index (100.00 → 110.00), the competitiveness index (90/100/100/110), a promotion's claimed 50.00% versus real 27.27%, and the basket totals.

## Local setup

**Prerequisites:** Python 3.11+, PostgreSQL 16 (Docker is the easiest source), `make`.

```bash
git clone https://github.com/5exclamations/retail-price-intelligence.git
cd retail-price-intelligence

python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # default: postgresql://rpi:rpi@localhost:5432/rpi

docker compose up -d postgres   # or use your own Postgres and set RPI_DATABASE_URL

make demo                       # migrate + generate 90 days of feeds + run the pipeline (~2 min)
make api                        # http://localhost:8000/docs
make dashboard                  # http://localhost:8501
```

Everything in one container stack instead: `docker compose up --build` (generates data, runs the pipeline, then serves the API on `:8000` and the dashboard on `:8501`).

Without Docker, create the database yourself:

```bash
sudo -u postgres psql -c "CREATE ROLE rpi LOGIN SUPERUSER PASSWORD 'rpi'" -c "CREATE DATABASE rpi OWNER rpi"
```

Useful targets (`make help` lists all): `make test`, `make lint`, `make seed`, `make run`, `make incident`, `make reset`, `make dbt-docs`, `make screenshots`.

Configuration is by environment variable: `RPI_DATABASE_URL`, `RPI_LANDING_DIR`, `RPI_TRUTH_DIR`, `RPI_REPORTS_DIR`, and `RPI_API_KEY` (at least 16 characters). **Without `RPI_API_KEY` the API's one write endpoint is disabled** (HTTP 503), and the dashboard's review buttons stay disabled unless `RPI_DASHBOARD_ALLOW_REVIEW=1`.

## Demo scenarios

**1. Cold start to dashboard.** `make demo`, then `make dashboard`. Pick a zone in the sidebar: zoned-chain prices appear only for the zone you choose.

**2. Incremental day.** Run the pipeline on the first 80 days, then again on all 90. The second run ingests only the 50 new files and appends only their observations; the third run changes nothing.

```bash
python -m rpi.cli run --as-of 2026-09-20 --up-to 2026-09-20
python -m rpi.cli run --as-of 2026-09-30        # files_ingested: 50
python -m rpi.cli run --as-of 2026-09-30        # files_ingested: 0, observations_inserted: 0
```

**3. A feed breaks.** Tomorrow's `caspianmart` file arrives with prices 100x too high.

```bash
make incident
```

Expected: the batch is rejected (`median price 389.00 AZN outside 0.30-50.00 AZN`), the other four feeds load, the run ends `degraded`, an alert is recorded and the freshness check flags `caspianmart`. See the **Data quality** page, or `SELECT * FROM ops.alert`.

**4. Work the review queue.**

```bash
python -m rpi.cli review list --limit 3
#   17  0.873  sumqayit  'Şokolad Südlü 200q' -> 'Ceyran Şokolad Südlü 200 Qr'
python -m rpi.cli review approve 17     # or: curl -X POST -H "X-API-Key: $RPI_API_KEY" -d '{"decision": "approve"}' localhost:8000/v1/matching/review/17
python -m rpi.cli run --as-of 2026-09-30   # gold relabels the SKU's whole history
```

**5. The zone rule.**

```bash
curl "localhost:8000/v1/products/283/prices"                      # zoned chain listed under excluded_price_points
curl "localhost:8000/v1/products/283/prices?zones=absheron:B"     # priced, for zone B only
```

**6. Honest promotions.** `curl "localhost:8000/v1/promotions?flagged=inflated"` returns promotions whose claimed discount exceeds the market-based one by more than 15 percentage points; `flagged=honest` returns the verified ones.

**7. Cheapest basket.** `curl localhost:8000/v1/basket/default?zones=absheron:A`, or `POST /v1/basket/cheapest` with `{"product_ids": [283, 98, 12], "zones": ["absheron:A"]}` for the best single store and the best split across stores.

**8. Score the matcher.** `python -m rpi.cli evaluate-matching` compares matches with the generator's ground truth.

**9. Crash safety.** Kill the pipeline after bronze, run it again: pending bronze batches are processed once and nothing is duplicated (covered by `test_a_crash_midway_resumes_without_duplicates`).

## Verified results

Measured on the development machine (Linux container, Python 3.13, PostgreSQL 16.15 on the same host) and on GitHub-hosted runners (Ubuntu, Python 3.12, `postgres:16` service). Timings are wall clock and will differ elsewhere. The data is synthetic, so analytical figures describe the generator, not any real market.

**Tests.** `python -m pytest rpi/tests` against a real PostgreSQL:

```text
123 passed in 132.42s            (development machine, fresh virtualenv of this repository)
ruff check rpi && ruff format --check rpi: all checks passed
```

By area: text and units 17, migrations 2, generator 8, bronze 4, silver 13, matching 17, gold 14 plus 5 exact-value, API 21, data quality 7, flow 5, dashboard 10.

**Continuous integration.** The same workflow files ran on GitHub Actions for the development branch before this repository was extracted: lint, 119 tests (the suite had 4 fewer API tests then), end-to-end pipeline on 45 days of data with a matching report and an API smoke test, `docker compose up --build` of the whole stack (about 4 minutes, image build included), gitleaks over the full history and pip-audit all passed. This repository's own first run will be on its first push.

**Docker.** Image build and `docker compose up` succeeded on the GitHub runner (above). They could not be run on the development machine, which has no Docker daemon; there the Dockerfile passes hadolint and `docker compose config`.

**Dataset (seed 42, `make seed`).** 5 fictional retailers; 320 canonical products; 1,077 retailer SKUs; 90 days (2026-07-03 to 2026-09-30); 448 price files plus one store file; 245,761 raw price records, of which 3,440 were rejected (all injected defects; per-reason counts equal the generator's manifest) and 242,321 became observations; 159,139 rows in the daily fact after zone collapse.

**Pipeline runs on that dataset (development machine).**

| Run | What it did | Result | Wall time |
|---|---|---|---|
| 1 | first 80 days, 399 files, 215,369 observations | `succeeded`, 29 checks passed, 62 dbt tests passed | 83 s |
| 2 | remaining 50 files, 26,952 new observations | `succeeded`, history before the window untouched | 45 s |
| 3 | no new files | `succeeded`, 0 files ingested, 0 observations inserted | 43 s |
| incident | `make incident`: one feed 100x too high | `degraded`, caspianmart batch rejected, other feeds loaded, 1 freshness warning | about 48 s |

Most of each run is dbt rebuilding the marts; ingesting 50 files takes seconds.

**Matching versus ground truth** (`python -m rpi.cli evaluate-matching`, pairwise, quarantined products excluded because users never see them):

| Metric | Value |
|---|---|
| Precision | 1.0000 (1,381 predicted pairs, all correct) |
| Recall | 0.9698 (1,424 true pairs) |
| Items matched by barcode / fingerprint / loose fingerprint / fuzzy | 286 / 438 / 1 / 18 |
| Items sent to the review queue | 22 (top candidate correct for 11) |
| Items quarantined | 12, in 2 products; all 3 injected barcode collisions caught |

These figures show the matcher behaves as designed on data whose difficulty was chosen by its author. They are a regression baseline, not a promise about real catalogues ([`docs/MATCHING.md`](docs/MATCHING.md)). The review queue's top candidate is right only half the time, which is why those items are not merged automatically.

**Headline analytics from the demo data** (structure recovered from what the generator builds in, such as a cheap chain at about -4% and a premium chain at +6%): all-category price index 100.00 to 101.95 over eleven weeks; fruit and vegetables +4.4%, household +0.7%; 5.9% of promotion price-days flagged as inflated; median claimed discount 21.6% against a median real discount of 18.7%.

**Not verified.** The dashboard's pages are checked by loading each one in a headless browser and asserting no exception (the screenshots come from that run), not by automated UI tests. Performance beyond this dataset size has not been measured.

## Technical trade-offs

* **Postgres for everything.** Simple and transactional, and enough for a quarter of a million rows. At tens of millions of rows the gold build would move to a columnar engine (dbt models are plain SQL and port with small changes); silver would stay Postgres.
* **Silver in Python, gold in dbt.** Parsing Azerbaijani unit abbreviations and fuzzy names is awkward in SQL and natural in Python; aggregation, indices and tests are natural in SQL. The seam is the `silver` schema, documented as dbt sources.
* **Plain SQL migrations instead of Alembic.** The platform owns its schemas outright, and a 60-line runner with checksum verification (an edited, already-applied migration is detected) is enough. Alembic would be the choice if downgrades or multiple branches of history were needed.
* **Marts rebuild each run, the fact is incremental.** `fct_price_daily` rewrites a 7-day window (late corrections) and leaves history alone; marts are `table` models rebuilt in seconds. The regular-price logic reads full history in a staging view, so incremental windows never starve it. Beyond roughly 10 M fact rows the marts would need their own incremental strategy.
* **Late corrections do not overwrite history.** If a corrected file for an existing day arrives, it becomes a new bronze batch and its overlapping observations are ignored (`ON CONFLICT DO NOTHING`), preserving append-only history. The alternative (versioned observations with a validity range) is more faithful and more expensive; the flow would count the ignored overlaps.
* **Zone price = the price most stores in the zone quote.** `mode()` stops one lagging store from moving a zone, and `zone_price_consistency` measures how often stores disagree.
* **Price index is fixed-base, matched-model, equally weighted.** It is inflation-like, not a CPI: no expenditure weights, no quality adjustment, no basket rotation. [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) states formulas and limits.
* **Matching thresholds were set on synthetic data.** They are configuration (`match_auto_threshold`, `match_review_threshold`) and the review queue produces the labelled pairs needed to recalibrate on real data.
* **The dashboard reads the database directly.** Fewer moving parts than going through the API; the API stays the contract for other consumers.
* **Reads are open, the single write endpoint fails closed.** `POST /v1/matching/review/{id}` returns 503 until `RPI_API_KEY` is set and 401 without the exact key (constant-time comparison); the reviewer recorded is always `api`, never client-supplied. There are no user accounts, rate limits or TLS in the app itself: anything exposed beyond localhost needs a gateway.
* **Prefect runs in-process.** No server or worker pool to operate for a single pipeline; moving to a deployment with schedules is a change in how the flow is launched, not in the flow.

## Interview talking points

**1. Layered ELT with a contract at every seam.** Bronze keeps every line as received with file hash and line number; silver guarantees that each raw row becomes either an observation or a stored, reasoned rejection (a test and a runtime check assert `bronze rows = observations + rejected`); gold is built only from silver by dbt. A bug is always located in exactly one layer.

**2. Idempotency is designed, not hoped for.** Files are keyed by `(source, sha256)`, observations by a unique index on `(item, COALESCE(store_id, 0), day)` (plain `UNIQUE` lets duplicates through because `NULL <> NULL`), the fact table rewrites a 7-day window, and a crash test proves a half-finished run resumes without duplicates.

**3. Money is an integer, history is append-only.** Prices are integer qepik end to end (converted once with `Decimal` and half-up rounding), discounts are integer basis points, and a database trigger rejects `UPDATE` and `DELETE` on observations. A dbt test fails the build if a money column is not an integer type.

**4. Judge discounts against the market, not the shelf tag.** The regular price is the last price actually observed without a promotion marker; a promotion's real discount compares against the median ordinary price at other chains that day. On the demo data 5.9% of promotion price-days are flagged as inflated (the generator inflates 5% of promotions).

**5. A price is a (retailer, zone) pair.** Zoned chains charge by zone, so the model never shows or compares a zoned chain without a chosen zone, and the API lists unpriced zones explicitly instead of guessing. The rule is enforced in a dbt test, the API and the dashboard.

**6. Entity resolution that knows when to ask.** A cascade (barcode, name fingerprint, fuzzy score) with hard rules that no score can override (size, fat percentage, brand, weighed-versus-packaged), a review queue for the uncertain band, and quarantine for merges that look wrong. Precision is tuned over recall because a wrong merge poisons price comparison while a missed one only hides a comparison.

**7. Test the SQL like code.** Statistical tests on noisy synthetic data cannot prove a mart is right, so marts are also tested on hand-built data to the qepik (index 100 to 110, discount 50.00% claimed against 27.27% real). The generator records every defect it injects, and tests assert silver rejected exactly those counts.

**8. Failure handling is observable.** Prefect retries transient failures; each step writes to an `ops` ledger; a feed in the wrong unit is rejected as a whole at a batch gate while the other feeds load (`degraded`, not `failed`); a thrown error writes a failure report. The incident is reproducible with `make incident`.

**9. Secure by default.** The single write endpoint returns 503 until a key is configured and compares keys in constant time; the dashboard's write buttons are off unless enabled; CI scans every commit for secrets and audits dependencies.

**10. Honest limits.** The data is synthetic, so analytical numbers describe the generator; matching thresholds were tuned on it; the index is unweighted and not a CPI. The README says so, and says what would change at 100x volume.

**Role fit.** *Data Engineer:* ingestion, idempotency, incremental loads, orchestration, observability. *Analytics Engineer:* dimensional model, dbt marts, snapshots, 62 tests, documented methodology. *Python Backend:* FastAPI contracts, psycopg pooling without an ORM, fail-closed write path, 123 tests against a real database.

## Data sources and licensing

* All data is **synthetic**, generated by `rpi/synth` from a seed. Retailers, brands, products and prices are invented and describe no real company.
* **No scraping code** and no data collected from real retailers is in this repository. The generator and the pipeline never contact a third-party site.
* Plugging in a real retailer means obtaining its feed under an agreement, writing one parser in `rpi/silver/parsers.py` and registering the retailer in `rpi/reference/retailers.csv`. Details: [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).
* Dependencies are audited with `pip-audit`; the repository is scanned for secrets on every push ([`SECURITY.md`](SECURITY.md)).

## Repository layout

```text
rpi/                    the platform (Python package)
  synth/                deterministic synthetic feeds, ground truth, incidents
  ingest/               bronze: landing files -> raw records with lineage
  silver/               parsers per source, validation, dedupe, enrichment, batch gates
  parsing/              Azerbaijani unit parsing and name fingerprints
  matching/             matcher, review actions, quarantine, evaluation against truth
  dq/                   data-quality checks
  flows/                pipeline steps and the Prefect flow
  api/                  FastAPI analytics API
  dashboard/            Streamlit app and its queries
  migrations/           SQL migrations (bronze, silver, ops)
  reference/            retailer registry, category map
  tests/                123 tests (real Postgres)
warehouse/              dbt project: staging, intermediate, marts, seeds, snapshots, tests
docs/                   methodology, matching, data quality, sources, screenshots, sample SQL
docker/, docker-compose.yml, Makefile, .github/workflows/ (ci.yml, security.yml)
```
