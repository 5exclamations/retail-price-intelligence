"""Runtime settings. Everything comes from the environment; defaults suit local development."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _path(env: str, default: Path) -> Path:
    return Path(os.environ.get(env, str(default)))


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "RPI_DATABASE_URL", "postgresql://rpi:rpi@localhost:5432/rpi"
        )
    )
    landing_dir: Path = field(
        default_factory=lambda: _path("RPI_LANDING_DIR", ROOT / "data/landing")
    )
    truth_dir: Path = field(default_factory=lambda: _path("RPI_TRUTH_DIR", ROOT / "data/truth"))
    dbt_dir: Path = field(default_factory=lambda: _path("RPI_DBT_DIR", ROOT / "warehouse"))
    reports_dir: Path = field(
        default_factory=lambda: _path("RPI_REPORTS_DIR", ROOT / "data/reports")
    )

    # Matching thresholds (see docs/MATCHING.md for how they were chosen).
    match_auto_threshold: float = 0.88
    match_review_threshold: float = 0.70

    # A batch whose median price falls outside this range is rejected as a whole.
    # Values are qepik (1 AZN = 100 qepik): 0.30 AZN .. 50 AZN.
    median_price_min: int = 30
    median_price_max: int = 5000


def get_settings() -> Settings:
    return Settings()
