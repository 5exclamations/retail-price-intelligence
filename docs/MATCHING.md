# Product matching

Goal: decide which retailer SKUs are the same physical product, with a confidence score, and send doubtful cases to a human instead of guessing. Code: `rpi/matching/matcher.py`.

## Cascade

Items are processed in a fixed order (barcode-bearing first), strongest evidence first:

| # | Method | Evidence | Confidence | Outcome |
|---|---|---|---|---|
| 1 | `ean` | same global barcode (valid GTIN check digit, not an in-store `2…` code) | 1.00 | auto |
| 2 | `fingerprint` | identical set of significant name tokens plus pack size, unit, fat % | 0.95 | auto |
| 3 | `loose_fingerprint` | same, tolerant of a space inside the brand (`Azər Süd` / `Azərsüd`) | 0.92 | auto |
| 4 | `fuzzy` | candidates blocked on unit, size and pack; score = 0.60 name similarity + 0.25 brand + 0.15 category | score | auto if >= 0.88 and 0.04 ahead of the runner-up; review if >= 0.70 |
| 5 | `new` | nothing close | 1.00 | new canonical product |

Name similarity is RapidFuzz `token_sort_ratio` on the fingerprint tokens. Fingerprints and unit parsing live in `rpi/parsing/` (Azerbaijani abbreviations: `qr` = grams, bare `kq` = weighed, `7 Lİ` = pack of 7).

## Hard rules (never traded against a score)

* A different pack size is a different product.
* Fat percentage and numeric variants (batteries 2025 vs 2032) must agree when both are present.
* Different brands never match; a missing brand on one side costs score and cannot reach auto-match on its own.
* Two SKUs of the same retailer are not merged by name evidence (identical names inside one chain almost always mean different articles); only a shared barcode may do that.
* Weighed goods match only weighed goods.
* Barcode wins over description: the same barcode with different descriptions is a description defect, not a reason to split. The quarantine step then checks the merge.

## Review queue

Items with a best score in [0.70, 0.88), or too close to the runner-up, become their own product (`pending_review`) and get up to three candidate rows in `silver.match_review` with the score and features. Nothing is merged on a guess. A reviewer approves (the SKU moves to the candidate product and the orphan product is removed) or rejects (when all candidates are rejected the item stays its own product and is not re-queued). Decisions come from the CLI, the API (`POST /v1/matching/review/{id}`, optional API key) or the dashboard, and reach gold on the next build because facts are keyed by SKU.

## Quarantine

After matching, a merge is quarantined, hidden from gold and the API, when its members disagree on pack size or unit (`size_conflict`), carry different barcodes (`ean_conflict`) or their typical regular prices differ by more than a factor of 3 (`price_spread`). Quarantine is recomputed on every run, so a fixed merge returns by itself.

## Measured quality (synthetic data only)

Against the generator's ground truth (seed 42, 1,077 SKUs): pairwise precision 1.0000, recall 0.9698; 22 SKUs in review (top candidate correct for 11); 12 SKUs in 2 quarantined products, including all 3 injected barcode collisions. Reproduce with `python -m rpi.cli evaluate-matching`.

Caveats: the generator's name noise (brand-less names, typos, word order, casing, units) is invented by the same author as the matcher, so recall and precision on a real catalogue will be lower. The thresholds (0.88 / 0.70, margin 0.04) are configuration; the pairs reviewers approve and reject are the labelled data needed to recalibrate them. Not covered: translations between scripts beyond the Turkic-to-ASCII folding, images, and matching across pack sizes (a 2 l bottle is never matched to a 1 l bottle by design).
