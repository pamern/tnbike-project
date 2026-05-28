"""Standalone AI scheduler for running after the ETL scheduler."""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from ai.common import setup_logging
from ai.run_ai_pipeline import run_pipeline


logger = setup_logging(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TNBIKE AI pipeline on an interval")
    parser.add_argument("--every-minutes", type=int, default=60)
    parser.add_argument("--branch", choices=["all", "bi", "predict", "report"], default="all")
    parser.add_argument("--date-from", default="2026-01-01")
    parser.add_argument("--date-to", default="2026-03-31")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run once and exit; useful when called by an external ETL scheduler.",
    )
    return parser.parse_args()


def run_once(args: argparse.Namespace) -> dict:
    logger.info("AI scheduler trigger at %s", datetime.now().isoformat(timespec="seconds"))
    return run_pipeline(
        branch=args.branch,
        date_from=args.date_from,
        date_to=args.date_to,
        dry_run=args.dry_run,
    )


def main() -> None:
    args = parse_args()
    if args.run_once:
        run_once(args)
        return

    sleep_seconds = max(args.every_minutes, 1) * 60
    while True:
        run_once(args)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()

