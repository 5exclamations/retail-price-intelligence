-- Silver: parsed, standardised, validated. Money is integer qepik (1 AZN = 100 qepik), never float.

CREATE TABLE IF NOT EXISTS silver.retailer (
    code         text PRIMARY KEY,
    name         text NOT NULL,
    -- single = one price list for the whole chain; zoned = price depends on the store's price zone
    price_model  text NOT NULL CHECK (price_model IN ('single', 'zoned'))
);

CREATE TABLE IF NOT EXISTS silver.store (
    id            bigserial PRIMARY KEY,
    retailer_code text NOT NULL REFERENCES silver.retailer(code),
    store_code    text NOT NULL,
    name          text NOT NULL,
    format        text,
    -- Measured from prices, not derived from the store name or format.
    price_zone    text,
    UNIQUE (retailer_code, store_code)
);

CREATE TABLE IF NOT EXISTS silver.store_item (
    id            bigserial PRIMARY KEY,
    retailer_code text NOT NULL REFERENCES silver.retailer(code),
    sku           text NOT NULL,
    name_raw      text NOT NULL,
    name_norm     text NOT NULL,
    brand         text,
    unit_value    numeric,         -- grams / millilitres / pieces; NULL for weighed goods
    unit_type     text CHECK (unit_type IN ('g', 'ml', 'pcs', 'kg_bulk')),
    pack          int,
    ean           text,
    ean_kind      text NOT NULL DEFAULT 'none' CHECK (ean_kind IN ('global', 'internal', 'none')),
    category_raw  text,
    category      text NOT NULL DEFAULT 'Uncategorized',
    first_seen    date NOT NULL,
    last_seen     date NOT NULL,
    UNIQUE (retailer_code, sku)
);
CREATE INDEX IF NOT EXISTS store_item_ean ON silver.store_item (ean) WHERE ean_kind = 'global';

-- Append-only price history. One row per item / store / day.
CREATE TABLE IF NOT EXISTS silver.price_observation (
    id             bigserial PRIMARY KEY,
    store_item_id  bigint NOT NULL REFERENCES silver.store_item(id),
    store_id       bigint REFERENCES silver.store(id),
    observed_date  date NOT NULL,
    observed_at    timestamptz NOT NULL,
    price_qepik    int NOT NULL CHECK (price_qepik > 0),
    old_price_qepik int CHECK (old_price_qepik IS NULL OR old_price_qepik > 0),
    available      boolean NOT NULL DEFAULT true,
    promo_until    date,
    batch_id       bigint NOT NULL REFERENCES bronze.ingest_batch(batch_id)
);
-- store_id is NULL for single-price chains and NULL <> NULL in a plain UNIQUE constraint,
-- so uniqueness goes through COALESCE (the classic NULL <> NULL trap).
CREATE UNIQUE INDEX IF NOT EXISTS price_observation_once
    ON silver.price_observation (store_item_id, COALESCE(store_id, 0), observed_date);
CREATE INDEX IF NOT EXISTS price_observation_date ON silver.price_observation (observed_date);

CREATE OR REPLACE FUNCTION silver.forbid_observation_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'silver.price_observation is append-only (% blocked)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS price_observation_append_only ON silver.price_observation;
CREATE TRIGGER price_observation_append_only
    BEFORE UPDATE OR DELETE ON silver.price_observation
    FOR EACH ROW EXECUTE FUNCTION silver.forbid_observation_change();

CREATE TABLE IF NOT EXISTS silver.rejected_record (
    id            bigserial PRIMARY KEY,
    batch_id      bigint NOT NULL REFERENCES bronze.ingest_batch(batch_id),
    row_num       int NOT NULL,
    source        text NOT NULL,
    reason        text NOT NULL,
    detail        text,
    payload       jsonb,
    rejected_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (batch_id, row_num)
);

-- Canonical products (the thing that is compared across retailers).
CREATE TABLE IF NOT EXISTS silver.product (
    id                bigserial PRIMARY KEY,
    name              text NOT NULL,
    brand             text,
    unit_value        numeric,
    unit_type         text,
    pack              int,
    category          text NOT NULL,
    ean               text,
    is_weighed        boolean NOT NULL DEFAULT false,
    -- Doubtful merge: never shown to users, never used in cross-retailer comparisons.
    quarantined       boolean NOT NULL DEFAULT false,
    quarantine_reason text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.product_match (
    store_item_id  bigint PRIMARY KEY REFERENCES silver.store_item(id),
    product_id     bigint NOT NULL REFERENCES silver.product(id),
    method         text NOT NULL,  -- ean | fingerprint | loose_fingerprint | fuzzy | new | review
    confidence     numeric(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    status         text NOT NULL CHECK (status IN ('auto', 'singleton', 'pending_review', 'approved')),
    matched_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS product_match_product ON silver.product_match (product_id);

CREATE TABLE IF NOT EXISTS silver.match_review (
    id                    bigserial PRIMARY KEY,
    store_item_id         bigint NOT NULL REFERENCES silver.store_item(id),
    candidate_product_id  bigint NOT NULL REFERENCES silver.product(id),
    score                 numeric(4, 3) NOT NULL,
    features              jsonb NOT NULL DEFAULT '{}'::jsonb,
    status                text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at            timestamptz NOT NULL DEFAULT now(),
    decided_at            timestamptz,
    decided_by            text,
    UNIQUE (store_item_id, candidate_product_id)
);
CREATE INDEX IF NOT EXISTS match_review_pending ON silver.match_review (score DESC) WHERE status = 'pending';
