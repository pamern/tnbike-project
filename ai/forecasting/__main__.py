"""CLI for TNBIKE forecasting."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ai.forecasting import close_db_pool, run_forecasting


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TNBIKE forecasting")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _trim_for_print(result: dict[str, Any]) -> dict[str, Any]:
    forecast = result["forecast_results"]
    churn = result["churn_results"]
    return {
        "dry_run": result["dry_run"],
        "feature_summary": result["feature_summary"],
        "forecast_summary": forecast.get("demand", {}).get("summary", {}),
        "color_summary": {
            "top_colors": forecast.get("colors", {}).get("top_colors", [])[:5],
            "rising_colors": forecast.get("colors", {}).get("rising_colors", [])[:5],
            "declining_colors": forecast.get("colors", {}).get("declining_colors", [])[:5],
        },
        "churn_summary": churn.get("summary", {}),
        "reasoning": result["reasoning"],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    try:
        result = run_forecasting(dry_run=args.dry_run)
        print(json.dumps(_trim_for_print(result), ensure_ascii=False, indent=2, default=str))
    finally:
        close_db_pool()


if __name__ == "__main__":
    main()
