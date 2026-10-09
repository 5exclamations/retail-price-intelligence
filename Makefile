# Retail Price Intelligence Platform: developer shortcuts. `make help` lists them.
PY ?= .venv/bin/python
AS_OF ?= 2026-09-30
-include .env
export

.DEFAULT_GOAL := help
.PHONY: help venv db-up db-down migrate seed run demo incident api dashboard test test-fast lint fmt dbt-docs screenshots reset clean

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

venv: ## create .venv and install the project with dev tools
	python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e ".[dev]"

db-up: ## start Postgres 16 in Docker (skip if you have your own: set RPI_DATABASE_URL)
	docker compose up -d postgres

db-down: ## stop Postgres
	docker compose down

migrate: ## apply SQL migrations
	$(PY) -m rpi.cli migrate

seed: ## generate 90 days of deterministic synthetic retailer feeds into data/landing
	$(PY) -m rpi.cli generate --days 90

run: ## run the whole pipeline once (bronze -> silver -> matching -> data quality -> dbt gold)
	$(PY) -m rpi.cli run --as-of $(AS_OF)

demo: migrate seed run ## migrate + seed + run: everything the API and dashboard need

incident: ## demo: tomorrow's caspianmart feed arrives in the wrong unit; the pipeline rejects it and reports
	$(PY) -m rpi.cli generate --days 91 --end-date 2026-10-01 --from-date 2026-10-01 --incident unit_mismatch:caspianmart:2026-10-01
	-$(PY) -m rpi.cli run --as-of 2026-10-01

api: ## serve the REST API on :8000 (docs at /docs)
	$(PY) -m uvicorn rpi.api.main:app --port 8000

dashboard: ## serve the Streamlit dashboard on :8501
	.venv/bin/streamlit run rpi/dashboard/app.py --server.port 8501

test: ## full test suite (needs Postgres; ~2.5 min)
	$(PY) -m pytest rpi/tests -q

test-fast: ## tests that need no database build
	$(PY) -m pytest rpi/tests -q -m "not slow"

lint: ## ruff lint + format check
	.venv/bin/ruff check rpi && .venv/bin/ruff format --check rpi

fmt: ## format the code
	.venv/bin/ruff check rpi --fix && .venv/bin/ruff format rpi

dbt-docs: ## generate dbt docs (lineage graph) into warehouse/target
	cd warehouse && RPI_PG_HOST=$${RPI_PG_HOST:-localhost} ../.venv/bin/dbt docs generate --profiles-dir .

screenshots: ## capture dashboard screenshots into docs/screenshots (dashboard must be running)
	$(PY) scripts/screenshots.py docs/screenshots

reset: ## DROP all platform schemas and re-migrate (development only)
	$(PY) -m rpi.cli reset

clean: ## remove generated data
	rm -rf data warehouse/target warehouse/logs
