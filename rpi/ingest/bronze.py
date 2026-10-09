"""Bronze ingestion: land files as-is, track the source, never lose a line.

Idempotency: a file is identified by (source, sha256). Loading it again is a no-op, so the flow can be
re-run after a crash at any point. A corrected file for the same day has different bytes and therefore
becomes a new batch; silver decides what to do with the overlap.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

from rpi import logging_setup

log = logging_setup.get(__name__)

_PRICES = re.compile(r"^(?P<source>[a-z_]+?)_(?P<date>\d{4}-\d{2}-\d{2})\.(?P<ext>jsonl|json|csv)$")
_STORES = re.compile(r"^(?P<source>[a-z_]+?)_stores_(?P<date>\d{4}-\d{2}-\d{2})\.json$")


@dataclass(frozen=True)
class LandingFile:
    path: Path
    source: str
    kind: str  # prices | stores
    business_date: date
    ext: str


def parse_landing_name(path: Path) -> LandingFile | None:
    m = _STORES.match(path.name)
    if m:
        return LandingFile(path, m["source"], "stores", date.fromisoformat(m["date"]), "json")
    m = _PRICES.match(path.name)
    if m:
        return LandingFile(path, m["source"], "prices", date.fromisoformat(m["date"]), m["ext"])
    return None


def discover(landing_dir: Path, *, up_to: date | None = None) -> list[LandingFile]:
    """All recognised landing files, stores first, then by date. Unrecognised files are ignored."""
    files = [
        f for p in sorted(landing_dir.rglob("*")) if p.is_file() and (f := parse_landing_name(p))
    ]
    if up_to:
        files = [f for f in files if f.business_date <= up_to]
    return sorted(files, key=lambda f: (f.kind != "stores", f.business_date, f.source))


def _records(f: LandingFile, raw: bytes) -> list[dict]:
    text = raw.decode("utf-8", errors="replace")
    if f.ext == "jsonl":
        out = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                out.append(value if isinstance(value, dict) else {"_unparseable": line[:500]})
            except json.JSONDecodeError:
                out.append({"_unparseable": line[:500]})
        return out
    if f.ext == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return [{"_unparseable": text[:500]}]
        return [
            r if isinstance(r, dict) else {"_unparseable": str(r)[:500]}
            for r in (data if isinstance(data, list) else [data])
        ]
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


def ingest_file(conn: psycopg.Connection, f: LandingFile) -> dict:
    """Load one file. Returns {'status': 'ingested'|'skipped', ...}. Caller owns the transaction."""
    raw = f.path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    seen = conn.execute(
        "SELECT batch_id FROM bronze.ingest_batch WHERE source = %s AND file_sha256 = %s",
        (f.source, sha),
    ).fetchone()
    if seen:
        log.info(
            "file already ingested",
            extra={"source": f.source, "file": f.path.name, "batch_id": seen["batch_id"]},
        )
        return {"status": "skipped", "batch_id": seen["batch_id"], "rows": 0}

    records = _records(f, raw)
    batch_id = conn.execute(
        """INSERT INTO bronze.ingest_batch (source, kind, landing_path, file_sha256, file_bytes, business_date, row_count)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING batch_id""",
        (f.source, f.kind, str(f.path), sha, len(raw), f.business_date, len(records)),
    ).fetchone()["batch_id"]
    with (
        conn.cursor() as cur,
        cur.copy(
            "COPY bronze.raw_record (batch_id, row_num, source, business_date, record_hash, payload) FROM STDIN"
        ) as cp,
    ):
        for i, rec in enumerate(records, start=1):
            body = json.dumps(rec, ensure_ascii=False, sort_keys=True)
            cp.write_row(
                (
                    batch_id,
                    i,
                    f.source,
                    f.business_date,
                    hashlib.sha1(body.encode()).hexdigest(),
                    body,
                )
            )
    log.info(
        "file ingested",
        extra={"source": f.source, "file": f.path.name, "batch_id": batch_id, "rows": len(records)},
    )
    return {"status": "ingested", "batch_id": batch_id, "rows": len(records)}
