"""Plain-SQL migration runner.

Each file in rpi/migrations is applied once, in name order, inside one transaction. The applied
checksum is stored so an edited, already-applied migration is detected instead of silently ignored.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from rpi import logging_setup
from rpi.db import connect

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
log = logging_setup.get(__name__)

_BOOTSTRAP = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.schema_migration (
    name text PRIMARY KEY,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


class MigrationChecksumError(RuntimeError):
    pass


def migrate(url: str | None = None) -> list[str]:
    """Apply pending migrations. Returns the names applied in this call."""
    applied_now: list[str] = []
    with connect(url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(727001)")  # serialise concurrent starters
        conn.execute(_BOOTSTRAP)
        done = {
            r["name"]: r["checksum"]
            for r in conn.execute("SELECT name, checksum FROM ops.schema_migration")
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            if path.name in done:
                if done[path.name] != checksum:
                    raise MigrationChecksumError(f"{path.name} was edited after it was applied")
                continue
            conn.execute(sql)
            conn.execute(
                "INSERT INTO ops.schema_migration (name, checksum) VALUES (%s, %s)",
                (path.name, checksum),
            )
            applied_now.append(path.name)
            log.info("migration applied", extra={"migration": path.name})
        conn.commit()
    return applied_now
