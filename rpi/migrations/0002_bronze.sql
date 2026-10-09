-- Bronze: bytes-as-received. Nothing is cleaned or dropped here.
-- Every row can be traced to a file (batch) and a line number inside it.
CREATE TABLE IF NOT EXISTS bronze.ingest_batch (
    batch_id       bigserial PRIMARY KEY,
    source         text NOT NULL,
    kind           text NOT NULL CHECK (kind IN ('prices', 'stores')),
    landing_path   text NOT NULL,
    file_sha256    text NOT NULL,
    file_bytes     bigint NOT NULL,
    business_date  date NOT NULL,
    row_count      int NOT NULL,
    status         text NOT NULL DEFAULT 'ingested'
                   CHECK (status IN ('ingested', 'silver_done', 'rejected', 'failed')),
    status_detail  text,
    ingested_at    timestamptz NOT NULL DEFAULT now(),
    silver_at      timestamptz,
    -- The same bytes from the same source are never loaded twice: this is the idempotency key.
    CONSTRAINT ingest_batch_file_once UNIQUE (source, file_sha256)
);
CREATE INDEX IF NOT EXISTS ingest_batch_status ON bronze.ingest_batch (status, business_date);

CREATE TABLE IF NOT EXISTS bronze.raw_record (
    batch_id       bigint NOT NULL REFERENCES bronze.ingest_batch(batch_id) ON DELETE CASCADE,
    row_num        int NOT NULL,
    source         text NOT NULL,
    business_date  date NOT NULL,
    record_hash    text NOT NULL,
    payload        jsonb NOT NULL,
    ingested_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (batch_id, row_num)
);
CREATE INDEX IF NOT EXISTS raw_record_source_date ON bronze.raw_record (source, business_date);
