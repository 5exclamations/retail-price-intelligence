"""Thin psycopg helpers. SQL stays SQL; no ORM."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from rpi.config import get_settings


def connect(url: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(
        url or get_settings().database_url, row_factory=dict_row, autocommit=autocommit
    )


@contextmanager
def transaction(url: str | None = None) -> Iterator[psycopg.Connection]:
    """Connection that commits on success and rolls back on any exception."""
    conn = connect(url)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
