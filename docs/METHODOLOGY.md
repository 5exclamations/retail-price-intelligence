# Methodology

All formulas below are implemented as dbt SQL in `warehouse/models/` and checked in `rpi/tests/test_gold_exact.py` on hand-built data. Money is integer qepik; ratios are computed from those integers.

## Price point

A *price point* is what a shopper faces: a single-price chain (`caspianmart:all`) or one measured zone of a zoned chain (`absheron:B`). Zones are measured from prices, never inferred from store names or formats. Stores of one zone are collapsed to one daily price with `mode()` (the price most stores quote), so one lagging store cannot move the zone; the share of disagreement is monitored by the `zone_price_consistency` check.

## Regular price

A retailer's "was" price is a claim. The **regular price** of an item on a promotion day is the last price the same item was observed at *without* a promotion marker. Only when a promotion starts before any ordinary price has been seen does the model fall back to the claimed old price, and the row is flagged `regular_price_source = 'claimed'`.

## Promotion honesty

For a promotion row (old price above price):

* `claimed_discount_bp = round((old - price) * 10000 / old)`
* `market_ref` = median, over *other* retailers, of the ordinary price of the same product that day
* `real_discount_bp = round((market_ref - price) * 10000 / market_ref)`
* the reference is **reliable** when `0.33 * old <= market_ref <= 3.0 * old` (a reference that far from the claim is more likely a data problem than a lie)
* `inflated_flag` = reliable and `claimed - real > 1500 bp`
* `fake_flag` = reliable and `price >= market_ref` (no saving against the market at all)
* `claim_vs_own_regular_bp` compares the shelf "was" with the item's own observed regular price; this is the history-based check that a single snapshot cannot make.

Limits: the market reference needs at least one other retailer selling the product that day; zoned chains are collapsed per retailer (median over zones) when used as a reference.

## Competitiveness

On products sold by at least three retailers in a week:

* weekly price per product and price point = mean regular price over days (weeks with fewer than 4 observed days are dropped)
* retailer price = median over the retailer's price points; market median = median over retailers
* `price_index_vs_market = 100 * exp(mean(ln(price_point_price / market_median)))` (geometric mean, equal weights)
* `share_cheapest_pct` = share of compared products where the price point is at or below every other retailer's price. **Ties count for every tied retailer**, so shares across retailers can exceed 100% in total; products with a common list price (about 60% in the synthetic data) are tied everywhere.

## Price index (inflation-like)

Fixed-base **matched-model Jevons index**:

1. Unit of observation: one product sold by one retailer (zones averaged first so a zoned chain counts once). Weekly average price in qepik, weeks with fewer than 4 observed days dropped.
2. Base period: the first complete week. Only (product, retailer) pairs priced in the base week take part.
3. `index_t = 100 * exp( mean over pairs of ln(p_t / p_base) )`: the geometric mean of price relatives, equal weights.
4. Two variants: **regular** (promotions removed, the underlying price trend) and **effective** (the shelf price including promotions). The effective index moves with promotion intensity relative to the base week.
5. Category indices use the same pairs restricted to the category; "All categories" pools all pairs. Week-over-week change is computed on the regular index.

Properties verified by tests: the base week is exactly 100.00; a uniform +10% move gives exactly 110.00.

What it is not: a CPI. There are no expenditure weights (a litre of milk counts as much as a tin of tuna), no quality adjustment, no replacement of discontinued products and no rotation of the basket. It is suitable for comparing categories and chains within this dataset; it should not be quoted as inflation.

## Cheapest basket

The default basket is built from data: per category, the two products stocked by the most retailers (up to every retailer), with quantities from the seed `basket_quantity.csv`. The daily cost of a price point is the integer sum of `quantity * lowest available price`. Only price points that stock **every** basket item that day are *complete* and ranked; incomplete ones are published without a rank so a store with a missing item cannot win by omission. The API also computes the best single store and the best split across stores for a custom list.

## Weighed goods

Products sold by weight (`kg_bulk`) have a per-kilogram price and no barcode. They match only other weighed goods and are compared kilogram to kilogram. Packaged products never merge with them (singular dbt test `assert_weighed_products_are_per_kg`).
