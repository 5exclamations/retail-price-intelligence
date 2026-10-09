"""Shared fixtures.

``built_db`` (session scope) builds one complete, realistic database - synthetic feeds with injected
defects, bronze, silver, matching, data-quality checks and the dbt gold layer - and every read-only test
shares it. Tests that change state (review decisions, incidents, whole-flow runs) get a private database
from ``fresh_db`` instead.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

# Keep Prefect's local state out of the developer's home directory and keep its output quiet.
_PREFECT_HOME = tempfile.mkdtemp(prefix="rpi-prefect-")
os.environ.setdefault("PREFECT_HOME", _PREFECT_HOME)
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "WARNING")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

PG_BASE = os.environ.get("RPI_TEST_PG_BASE", "postgresql://rpi:rpi@localhost:5432")
END = date(2026, 9, 30)


def make_database(name: str) -> str:
    assert re.fullmatch(r"[a-z0-9_]+", name)
    with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')
    return f"{PG_BASE}/{name}"


@contextmanager
def use_database(
    url: str, landing: Path | None = None, truth: Path | None = None
) -> Iterator[None]:
    keys = {"RPI_DATABASE_URL": url}
    if landing:
        keys["RPI_LANDING_DIR"] = str(landing)
    if truth:
        keys["RPI_TRUTH_DIR"] = str(truth)
    old = {k: os.environ.get(k) for k in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def generate_feeds(
    landing: Path,
    truth: Path,
    *,
    days: int = 21,
    products: int = 90,
    clean: bool = False,
    incidents: list[str] | None = None,
    end: date = END,
    **kw,
) -> dict:
    from rpi.synth.generate import GenConfig, generate

    return generate(
        GenConfig(
            n_products=products,
            days=days,
            end_date=end,
            landing_dir=landing,
            truth_dir=truth,
            clean=clean,
            incidents=incidents or [],
            **kw,
        )
    )


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, row_factory=dict_row)


@pytest.fixture(scope="session")
def feeds(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("feeds")
    manifest = generate_feeds(root / "landing", root / "truth", days=35, products=100)
    return {"landing": root / "landing", "truth": root / "truth", "manifest": manifest}


@pytest.fixture(scope="session")
def built_db(feeds) -> Iterator[dict]:
    """A fully built platform database. Read-only for tests."""
    from rpi.flows import steps
    from rpi.migrate import migrate
    from rpi.warehouse import dbt

    url = make_database("rpi_test_main")
    with use_database(url, feeds["landing"], feeds["truth"]):
        migrate()
        ingest = steps.ingest_step()
        silver = steps.silver_step("test-run")
        match = steps.match_step()
        with connect(url) as conn:
            conn.execute(
                "INSERT INTO ops.pipeline_run (run_id, flow_name, status, as_of_date) VALUES ('test-run', 'tests', 'running', %s)",
                (END,),
            )
            conn.commit()
        dq = steps.dq_step("test-run", END)
        dbt("snapshot")
        summary = dbt("build")
        yield {
            "url": url,
            "ingest": ingest,
            "silver": silver,
            "match": match,
            "dq": dq,
            "dbt": summary,
            **feeds,
        }


@pytest.fixture()
def db(built_db) -> Iterator[psycopg.Connection]:
    """Read connection to the shared database (everything is rolled back at the end of the test)."""
    with (
        use_database(built_db["url"], built_db["landing"], built_db["truth"]),
        connect(built_db["url"]) as conn,
    ):
        yield conn
        conn.rollback()


@pytest.fixture()
def fresh_db(request, tmp_path) -> Iterator[dict]:
    """Private, empty, migrated database plus an empty landing directory."""
    from rpi.migrate import migrate

    name = "rpi_t_" + re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:40]
    url = make_database(name)
    landing, truth = tmp_path / "landing", tmp_path / "truth"
    with use_database(url, landing, truth):
        migrate()
        yield {"url": url, "landing": landing, "truth": truth}
