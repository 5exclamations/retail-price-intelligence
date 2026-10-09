from __future__ import annotations

import shutil

import pytest

from rpi import migrate as m


def test_migrations_apply_once_and_are_idempotent(fresh_db):
    assert m.migrate() == []  # fresh_db already applied everything
    from rpi.tests.conftest import connect

    with connect(fresh_db["url"]) as conn:
        names = [
            r["name"] for r in conn.execute("SELECT name FROM ops.schema_migration ORDER BY name")
        ]
    assert names == sorted(p.name for p in m.MIGRATIONS_DIR.glob("*.sql"))


def test_editing_an_applied_migration_is_detected(fresh_db, tmp_path, monkeypatch):
    copy = tmp_path / "migrations"
    shutil.copytree(m.MIGRATIONS_DIR, copy)
    first = sorted(copy.glob("*.sql"))[0]
    first.write_text(first.read_text() + "\n-- sneaky edit\n")
    monkeypatch.setattr(m, "MIGRATIONS_DIR", copy)
    with pytest.raises(m.MigrationChecksumError):
        m.migrate()
