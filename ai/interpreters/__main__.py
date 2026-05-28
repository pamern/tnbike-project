"""CLI for BI interpreter dry-runs."""

from __future__ import annotations

import argparse
import json
from datetime import date

from ai.interpreters import close_db_pool, run_bi_interpretation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TNBIKE BI interpretation")
    parser.add_argument("--date-from", default="2026-01-01")
    parser.add_argument("--date-to", default="2026-03-31")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = run_bi_interpretation(
            date_from=args.date_from,
            date_to=args.date_to,
            dry_run=args.dry_run,
        )
        printable = {
            "date_from": result["date_from"],
            "date_to": result["date_to"],
            "dry_run": result["dry_run"],
            "data_summary": result["data_summary"],
            "interpretation": result["interpretation"],
        }
        print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))
    finally:
        close_db_pool()


if __name__ == "__main__":
    main()

