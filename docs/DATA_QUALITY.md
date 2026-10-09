# Data quality

Quality is enforced at four points. Each catches a different class of damage, and each result is stored, so "is the data healthy?" is a query, not an opinion.

## 1. Row level (silver)

Every raw price record ends as an observation or a rejected record (`silver.rejected_record`, with reason and payload):

| Reason | Meaning |
|---|---|
| `unparseable_line` | broken JSON, kept in bronze as `_unparseable` |
| `unexpected_shape` | parser could not read the record |
| `missing_sku`, `missing_name` | identity fields empty |
| `invalid_price` | not a number |
| `nonpositive_price` | zero or negative |
| `unknown_store` | zoned retailer row with a store that has no registered zone |
| `duplicate_row` | identical repeat of a kept row |
| `conflicting_duplicate` | same SKU and store, different price; the first row wins |

Money is converted once, with `Decimal` and half-up rounding, to integer qepik. A "was" price that is not above the current price is dropped (not a promotion).

## 2. Batch level (gates)

A file that is wrong as a whole is refused before any of its rows enter silver; bronze keeps it, the batch is marked `rejected`, an `error` alert is raised and the rest of the run continues.

| Gate | Threshold |
|---|---|
| `unknown_source` | source not in `rpi/reference/retailers.csv` |
| `empty_batch` | no records |
| `reject_ratio` | more than 20% of rows invalid |
| `median_price_out_of_range` | median price outside 0.30 to 50 AZN (a feed in the wrong unit, or a store from another market) |
| `row_count_collapse` | fewer than 50% of the trailing median rows |

## 3. Run level (`rpi/dq/checks.py`)

Stored in `ops.dq_result` with `severity` `warn` or `error`. Any failing `error` check marks the run `degraded`.

| Layer | Check | Severity |
|---|---|---|
| bronze | `row_reconciliation`: file rows = observations + rejected rows | error |
| bronze | `no_stuck_batches`: nothing left `ingested` or `failed` | error |
| bronze | `row_count_vs_trailing_median` per source (>= 0.70) | warn |
| silver | `freshness_days` per retailer: a file for the as-of date | warn, error beyond 3 days |
| silver | `median_price_in_range` per retailer | error |
| silver | `no_duplicate_observations` | error |
| silver | `reject_ratio_latest_day` per source (<= 5%) | warn |
| silver | `zone_price_consistency`: stores of a zone quote one price (<= 1% disagree) | warn |
| silver | `categories_mapped`, `unit_parse_coverage` (>= 95%) | warn |
| silver | `review_backlog` (<= 100), `quarantine_rate` (<= 5%) | warn |
| silver | `implausible_price_jumps`: day-over-day beyond x2.5 or x0.4 (<= 0.5%) | warn |
| gold | `dbt_tests`: result of the dbt build | error |

## 4. Model level (dbt)

62 tests, 5 of them singular SQL tests: unique and not-null keys, `relationships`, accepted values, `positive` and `integer_typed` on money columns, `unique_combination` for the fact grain, a range test on indices, a rule that zoned chains always have a zone and single-price chains never do, base week index = 100, no quarantined product in gold, no promotion with old price at or below price, weighed and packaged goods never share a product.

## Why the checks can be trusted

* The generator writes a manifest of every defect it injected; tests assert the rejected counts per reason equal it exactly.
* Gates are exercised by generating real incident files (`unit_mismatch`, `truncated`) and running the pipeline.
* Marts are verified to the qepik on hand-built data.
* Append-only history is a database trigger, tested by attempting an `UPDATE` and a `DELETE`.

## Failure reporting

Exceptions mark the run `failed`, add an alert and write `data/reports/failure_<run_id>.json` (error, traceback, steps completed, alerts). Logs are JSON lines with `run_id`, `source` and `batch_id` fields. Run history, step durations, checks and alerts are in the `ops` schema and on the dashboard's Data quality page.
