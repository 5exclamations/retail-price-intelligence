"""Score the matcher against the generator's ground truth (synthetic data only).

Pairwise metrics: every pair of store items that share a canonical product is a predicted match; every
pair that shares a true product is a real match. Precision = correct predicted pairs / predicted pairs,
recall = correct predicted pairs / real pairs. Quarantined products are excluded from the predictions
because they never reach users.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import psycopg


def _pairs(n: int) -> int:
    return n * (n - 1) // 2


def evaluate(conn: psycopg.Connection, truth_dir: Path) -> dict:
    truth = {}
    with open(truth_dir / "items.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            truth[(r["retailer_code"], r["sku"])] = int(r["pid"])

    rows = conn.execute(
        """SELECT si.retailer_code, si.sku, m.product_id, m.method, m.status, p.quarantined
           FROM silver.product_match m JOIN silver.store_item si ON si.id = m.store_item_id
           JOIN silver.product p ON p.id = m.product_id"""
    ).fetchall()
    known = [
        (r, truth[(r["retailer_code"], r["sku"])])
        for r in rows
        if (r["retailer_code"], r["sku"]) in truth
    ]

    def score(selected) -> dict:
        by_pred, by_true, by_both = Counter(), Counter(), Counter()
        for r, pid in selected:
            by_pred[r["product_id"]] += 1
            by_true[pid] += 1
            by_both[(r["product_id"], pid)] += 1
        predicted = sum(_pairs(n) for n in by_pred.values())
        real = sum(_pairs(n) for n in by_true.values())
        tp = sum(_pairs(n) for n in by_both.values())
        p = tp / predicted if predicted else 1.0
        rc = tp / real if real else 1.0
        return {
            "predicted_pairs": predicted,
            "true_pairs": real,
            "correct_pairs": tp,
            "precision": round(p, 4),
            "recall": round(rc, 4),
            "f1": round(2 * p * rc / (p + rc), 4) if p + rc else 0.0,
        }

    published = [(r, pid) for r, pid in known if not r["quarantined"]]
    by_method = Counter(r["method"] for r, _ in known)

    # How good is the review queue? For items sent to review, is the best candidate actually right?
    q = conn.execute(
        """SELECT si.retailer_code, si.sku, rv.candidate_product_id, rv.score
           FROM silver.match_review rv JOIN silver.store_item si ON si.id = rv.store_item_id
           WHERE rv.status = 'pending' ORDER BY rv.store_item_id, rv.score DESC"""
    ).fetchall()
    # truth product of a canonical product = truth pid of any of its members
    rep = {}
    for r, pid in known:
        rep.setdefault(r["product_id"], pid)
    seen, hit, total = set(), 0, 0
    for r in q:
        key = (r["retailer_code"], r["sku"])
        if key in seen or key not in truth:
            continue
        seen.add(key)
        total += 1
        hit += rep.get(r["candidate_product_id"]) == truth[key]
    return {
        "items_evaluated": len(known),
        "published_pairs": score(published),
        "all_pairs": score(known),
        "by_method": dict(by_method),
        "quarantined_items": sum(1 for r, _ in known if r["quarantined"]),
        "review_queue": {
            "items": total,
            "top_candidate_correct": hit,
            "top_candidate_precision": round(hit / total, 4) if total else None,
        },
    }
