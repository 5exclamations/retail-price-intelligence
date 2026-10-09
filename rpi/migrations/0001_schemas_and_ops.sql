-- Layers:  bronze (raw, source-tracked) -> silver (clean, validated) -> gold (dbt marts)
-- staging holds dbt views over silver; ops holds orchestration and data-quality telemetry.
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.pipeline_run (
    run_id       text PRIMARY KEY,
    flow_name    text NOT NULL,
    status       text NOT NULL CHECK (status IN ('running', 'succeeded', 'degraded', 'failed')),
    as_of_date   date,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    stats        jsonb NOT NULL DEFAULT '{}'::jsonb,
    error        text
);

CREATE TABLE IF NOT EXISTS ops.step_run (
    id           bigserial PRIMARY KEY,
    run_id       text NOT NULL REFERENCES ops.pipeline_run(run_id) ON DELETE CASCADE,
    step         text NOT NULL,
    status       text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'skipped')),
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    attempts     int NOT NULL DEFAULT 1,
    stats        jsonb NOT NULL DEFAULT '{}'::jsonb,
    error        text
);

CREATE TABLE IF NOT EXISTS ops.dq_result (
    id           bigserial PRIMARY KEY,
    run_id       text,
    layer        text NOT NULL CHECK (layer IN ('bronze', 'silver', 'gold')),
    check_name   text NOT NULL,
    scope        text NOT NULL DEFAULT 'all',
    severity     text NOT NULL CHECK (severity IN ('warn', 'error')),
    passed       boolean NOT NULL,
    observed     double precision,
    threshold    text,
    detail       text,
    checked_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dq_result_run ON ops.dq_result (run_id);

CREATE TABLE IF NOT EXISTS ops.alert (
    id           bigserial PRIMARY KEY,
    run_id       text,
    severity     text NOT NULL CHECK (severity IN ('warn', 'error')),
    source       text,
    title        text NOT NULL,
    detail       text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
