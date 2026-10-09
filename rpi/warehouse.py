"""Run the dbt project (gold layer) from Python and fold its results into the data-quality ledger."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from psycopg.conninfo import conninfo_to_dict

from rpi import logging_setup
from rpi.config import get_settings

log = logging_setup.get(__name__)


class DbtFailure(RuntimeError):
    pass


@dataclass
class DbtSummary:
    models: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    failures: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "models": self.models,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "failures": self.failures,
        }


def _export_connection_env(url: str | None) -> None:
    info = conninfo_to_dict(url or get_settings().database_url)
    os.environ.setdefault("RPI_PG_HOST", "localhost")
    for env, key in (
        ("RPI_PG_HOST", "host"),
        ("RPI_PG_PORT", "port"),
        ("RPI_PG_USER", "user"),
        ("RPI_PG_PASSWORD", "password"),
        ("RPI_PG_DBNAME", "dbname"),
    ):
        if key in info:
            os.environ[env] = str(info[key])


def dbt(*args: str, url: str | None = None, vars: dict | None = None) -> DbtSummary:
    """Invoke dbt in-process. Raises DbtFailure when any model or test fails."""
    from dbt.cli.main import dbtRunner

    settings = get_settings()
    _export_connection_env(url)
    cli = [
        *args,
        "--project-dir",
        str(settings.dbt_dir),
        "--profiles-dir",
        str(settings.dbt_dir),
        "--quiet",
    ]
    if vars:
        cli += ["--vars", str(vars)]
    res = dbtRunner().invoke(cli)
    summary = DbtSummary()
    for r in getattr(res.result, "results", None) or []:
        node = r.node
        status = str(r.status)
        if node.resource_type.value == "test":
            if status in ("pass", "warn"):
                summary.tests_passed += 1
            else:
                summary.tests_failed += 1
                summary.failures.append(
                    {
                        "test": node.name,
                        "status": status,
                        "failures": r.failures,
                        "message": (r.message or "")[:300],
                    }
                )
        else:
            summary.models += 1
            if status not in ("success", "pass"):
                summary.failures.append(
                    {"model": node.name, "status": status, "message": (r.message or "")[:300]}
                )
    log.info(
        "dbt finished",
        extra={"command": args[0], **summary.as_dict() | {"failures": len(summary.failures)}},
    )
    if not res.success:
        detail = str(res.exception) if res.exception else f"{len(summary.failures)} failing node(s)"
        raise DbtFailure(
            f"dbt {' '.join(args)} failed: {detail}; first failures: {summary.failures[:3]}"
        )
    return summary
