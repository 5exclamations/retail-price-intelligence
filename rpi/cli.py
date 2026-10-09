"""Command line entry point: ``python -m rpi.cli <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from rpi import logging_setup
from rpi.config import get_settings


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rpi", description="Retail Price Intelligence Platform")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="apply SQL migrations")
    sub.add_parser("reset", help="DROP all platform schemas and re-migrate (development only)")

    g = sub.add_parser("generate", help="write deterministic synthetic retailer feeds")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--products", type=int, default=320)
    g.add_argument("--days", type=int, default=90)
    g.add_argument("--end-date", type=_date, default=date(2026, 9, 30))
    g.add_argument(
        "--from-date", type=_date, default=None, help="write only files from this day on"
    )
    g.add_argument("--to-date", type=_date, default=None, help="write only files up to this day")
    g.add_argument(
        "--incident",
        action="append",
        default=[],
        metavar="KIND:SOURCE:DATE",
        help="inject a feed incident, e.g. unit_mismatch:caspianmart:2026-09-30 (or truncated)",
    )
    g.add_argument("--landing-dir", type=Path, default=None)
    g.add_argument("--truth-dir", type=Path, default=None)

    r = sub.add_parser("run", help="run the Prefect pipeline once")
    r.add_argument("--as-of", type=_date, default=None)
    r.add_argument(
        "--up-to", type=_date, default=None, help="ingest landing files up to this date only"
    )
    r.add_argument("--landing-dir", type=Path, default=None)
    r.add_argument("--no-dbt", action="store_true")

    for name in ("ingest", "silver", "match", "dq"):
        s = sub.add_parser(name, help=f"run only the {name} step")
        if name == "dq":
            s.add_argument("--as-of", type=_date, default=None)

    d = sub.add_parser("dbt", help="run a dbt command against the platform database")
    d.add_argument("dbt_args", nargs="+")

    rv = sub.add_parser("review", help="work the match review queue")
    rv.add_argument("action", choices=["list", "approve", "reject"])
    rv.add_argument("review_id", nargs="?", type=int)
    rv.add_argument("--limit", type=int, default=20)

    sub.add_parser("evaluate-matching", help="score matching against synthetic ground truth")
    args = p.parse_args(argv)
    logging_setup.configure()
    settings = get_settings()

    if args.cmd == "migrate":
        from rpi.migrate import migrate

        print(json.dumps({"applied": migrate()}))
    elif args.cmd == "reset":
        from rpi.db import connect
        from rpi.migrate import migrate

        with connect() as conn:
            for schema in ("bronze", "silver", "staging", "gold", "ops"):
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.commit()
        print(json.dumps({"applied": migrate()}))
    elif args.cmd == "generate":
        from rpi.synth.generate import GenConfig, generate

        cfg = GenConfig(
            seed=args.seed,
            n_products=args.products,
            days=args.days,
            end_date=args.end_date,
            from_date=args.from_date,
            to_date=args.to_date,
            incidents=args.incident,
            landing_dir=args.landing_dir or settings.landing_dir,
            truth_dir=args.truth_dir or settings.truth_dir,
        )
        print(json.dumps(generate(cfg), indent=1))
    elif args.cmd == "run":
        from rpi.flows.pipeline import daily_pipeline

        out = daily_pipeline(
            as_of=args.as_of,
            landing_dir=str(args.landing_dir) if args.landing_dir else None,
            run_dbt=not args.no_dbt,
            up_to=args.up_to,
        )
        print(json.dumps(out, indent=1, default=str))
        return 0 if out["status"] != "failed" else 1
    elif args.cmd in ("ingest", "silver", "match", "dq"):
        from rpi.flows import steps
        from rpi.migrate import migrate

        migrate()
        if args.cmd == "ingest":
            out = steps.ingest_step()
            out.pop("batch_ids")
        elif args.cmd == "silver":
            out = steps.silver_step()
        elif args.cmd == "match":
            out = steps.match_step()
        else:
            out = steps.dq_step(None, args.as_of or date.today())
        print(json.dumps(out, indent=1, default=str))
    elif args.cmd == "dbt":
        from rpi.warehouse import dbt

        print(json.dumps(dbt(*args.dbt_args).as_dict(), indent=1))
    elif args.cmd == "review":
        from rpi.db import connect
        from rpi.matching.matcher import approve_review, reject_review

        with connect() as conn:
            if args.action == "list":
                rows = conn.execute(
                    """SELECT rv.id, rv.score, si.retailer_code, si.name_raw AS item, p.name AS candidate
                       FROM silver.match_review rv JOIN silver.store_item si ON si.id = rv.store_item_id
                       JOIN silver.product p ON p.id = rv.candidate_product_id
                       WHERE rv.status = 'pending' ORDER BY rv.score DESC, rv.id LIMIT %s""",
                    (args.limit,),
                ).fetchall()
                for r in rows:
                    print(
                        f"{r['id']:>5}  {float(r['score']):.3f}  {r['retailer_code']:<12} {r['item']!r:<48} -> {r['candidate']!r}"
                    )
            else:
                if args.review_id is None:
                    p.error("review_id is required")
                fn = approve_review if args.action == "approve" else reject_review
                print(json.dumps(fn(conn, args.review_id, decided_by="cli")))
                conn.commit()
    elif args.cmd == "evaluate-matching":
        from rpi.db import connect
        from rpi.matching.evaluate import evaluate

        with connect() as conn:
            print(json.dumps(evaluate(conn, settings.truth_dir), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
