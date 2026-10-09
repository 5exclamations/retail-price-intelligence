"""The generator must be reproducible: same seed, same bytes."""

from __future__ import annotations

import csv
import hashlib
from datetime import date
from pathlib import Path

from rpi.synth.catalog import build_catalog
from rpi.synth.generate import RETAILERS
from rpi.tests.conftest import generate_feeds
from rpi.text import valid_gtin


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def test_same_seed_gives_identical_files(tmp_path):
    generate_feeds(tmp_path / "a", tmp_path / "ta", days=6, products=40)
    generate_feeds(tmp_path / "b", tmp_path / "tb", days=6, products=40)
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")


def test_different_seed_gives_different_files(tmp_path):
    generate_feeds(tmp_path / "a", tmp_path / "ta", days=4, products=40)
    generate_feeds(tmp_path / "b", tmp_path / "tb", days=4, products=40, seed=7)
    assert _digest(tmp_path / "a") != _digest(tmp_path / "b")


def test_extending_the_window_continues_the_same_price_history(tmp_path):
    """'Tomorrow's files' must be consistent with today's, or incremental runs would see fake jumps."""
    generate_feeds(tmp_path / "short", tmp_path / "t1", days=10, products=40, end=date(2026, 9, 29))
    generate_feeds(tmp_path / "long", tmp_path / "t2", days=11, products=40, end=date(2026, 9, 30))
    shared = [p for p in (tmp_path / "short").rglob("*") if p.is_file()]
    assert shared
    for p in shared:
        assert (
            p.read_bytes() == (tmp_path / "long" / p.relative_to(tmp_path / "short")).read_bytes()
        )


def test_writing_a_sub_window_writes_only_those_days(tmp_path):
    generate_feeds(
        tmp_path / "x",
        tmp_path / "t",
        days=10,
        products=30,
        from_date=date(2026, 9, 28),
        to_date=date(2026, 9, 29),
    )
    days = {
        p.stem.split("_")[-1]
        for p in (tmp_path / "x").rglob("*")
        if p.is_file() and "stores" not in p.name
    }
    assert days == {"2026-09-28", "2026-09-29"}


def test_each_retailer_has_its_own_format(feeds):
    exts = {r: {p.suffix for p in (feeds["landing"] / r).glob("*_20*")} for r in RETAILERS}
    assert exts["baku_fresh"] == {".jsonl"}
    assert exts["absheron"] == {".json"}
    assert exts["shirvan"] == {".csv"}


def test_catalog_barcodes_are_valid_and_unique():
    catalog = build_catalog(200, 42)
    eans = [p.ean for p in catalog if p.ean]
    assert all(valid_gtin(e) for e in eans)
    assert len(eans) == len(set(eans))
    assert all(p.ean is None for p in catalog if p.weighed)


def test_ground_truth_covers_every_item(feeds):
    with open(feeds["truth"] / "items.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == feeds["manifest"]["items"]
    assert len({(r["retailer_code"], r["sku"]) for r in rows}) == len(
        rows
    )  # SKUs unique per retailer


def test_clean_mode_injects_nothing(tmp_path):
    m = generate_feeds(tmp_path / "c", tmp_path / "t", days=5, products=40, clean=True)
    assert m["injected"] == {}
