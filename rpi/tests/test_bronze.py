from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from rpi.ingest.bronze import discover, ingest_file, parse_landing_name
from rpi.tests.conftest import connect


def test_landing_names_are_parsed():
    f = parse_landing_name(Path("x/baku_fresh_2026-09-30.jsonl"))
    assert (f.source, f.kind, f.business_date) == ("baku_fresh", "prices", date(2026, 9, 30))
    s = parse_landing_name(Path("absheron_stores_2026-07-03.json"))
    assert (s.source, s.kind) == ("absheron", "stores")
    assert parse_landing_name(Path("notes.txt")) is None


def test_ingest_is_idempotent_and_tracks_source(fresh_db, tmp_path):
    from rpi.tests.conftest import generate_feeds

    generate_feeds(fresh_db["landing"], fresh_db["truth"], days=3, products=30, clean=True)
    files = discover(fresh_db["landing"])
    with connect(fresh_db["url"]) as conn:
        first = [ingest_file(conn, f) for f in files]
        conn.commit()
        rows_after_first = conn.execute("SELECT count(*) AS n FROM bronze.raw_record").fetchone()[
            "n"
        ]
        second = [ingest_file(conn, f) for f in files]
        conn.commit()
        rows_after_second = conn.execute("SELECT count(*) AS n FROM bronze.raw_record").fetchone()[
            "n"
        ]
        sources = {
            r["source"] for r in conn.execute("SELECT DISTINCT source FROM bronze.ingest_batch")
        }
        batch = conn.execute(
            "SELECT * FROM bronze.ingest_batch ORDER BY batch_id LIMIT 1"
        ).fetchone()
    assert all(r["status"] == "ingested" for r in first)
    assert all(r["status"] == "skipped" for r in second)
    assert rows_after_first == rows_after_second > 0
    assert sources == {"baku_fresh", "caspianmart", "absheron", "shirvan", "sumqayit"}
    assert len(batch["file_sha256"]) == 64 and batch["landing_path"].endswith(
        ("jsonl", "json", "csv")
    )


def test_a_corrected_file_becomes_a_new_batch(fresh_db):
    f = fresh_db["landing"] / "baku_fresh" / "baku_fresh_2026-09-30.jsonl"
    f.parent.mkdir(parents=True)
    row = {
        "sku": "A",
        "title": "Süd 1 l",
        "price": "1.00",
        "was": None,
        "in_stock": True,
        "scraped_at": "2026-09-30T06:00:00",
    }
    f.write_text(json.dumps(row) + "\n")
    lf = parse_landing_name(f)
    with connect(fresh_db["url"]) as conn:
        a = ingest_file(conn, lf)
        f.write_text(json.dumps({**row, "price": "1.10"}) + "\n")
        b = ingest_file(conn, lf)
        conn.commit()
    assert a["status"] == b["status"] == "ingested" and a["batch_id"] != b["batch_id"]


def test_broken_lines_are_kept_not_dropped(fresh_db):
    f = fresh_db["landing"] / "baku_fresh" / "baku_fresh_2026-09-30.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(
        '{"sku": "A", "title": "x", "price": "1.00"}\n{"sku": "B", "title": "trunc\n\n["not", "a", "dict"]\n'
    )
    with connect(fresh_db["url"]) as conn:
        res = ingest_file(conn, parse_landing_name(f))
        conn.commit()
        payloads = [
            r["payload"]
            for r in conn.execute("SELECT payload FROM bronze.raw_record ORDER BY row_num")
        ]
    assert res["rows"] == 3
    assert "_unparseable" in payloads[1] and "_unparseable" in payloads[2]
