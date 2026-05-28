"""CLI entrypoint for the TNBIKE AI pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ai.common import setup_logging
from ai.forecasting import close_db_pool as close_forecast_pool
from ai.forecasting import run_forecasting
from ai.interpreters import close_db_pool as close_bi_pool
from ai.interpreters import run_bi_interpretation
from ai.report.report_runner import run_report


logger = setup_logging(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TNBIKE AI pipeline")
    parser.add_argument(
        "--branch",
        choices=["all", "bi", "predict", "report"],
        default="all",
        help="Pipeline branch to run",
    )
    parser.add_argument("--date-from", default="2026-01-01")
    parser.add_argument("--date-to", default="2026-03-31")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _compact_bi(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "date_from": result.get("date_from"),
        "date_to": result.get("date_to"),
        "dry_run": result.get("dry_run"),
        "data_summary": result.get("data_summary"),
        "interpretation_status": result.get("interpretation", {}).get("status"),
    }


def _compact_forecast(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "dry_run": result.get("dry_run"),
        "feature_summary": result.get("feature_summary"),
        "forecast_summary": result.get("forecast_results", {}).get("demand", {}).get("summary", {}),
        "churn_summary": result.get("churn_results", {}).get("summary", {}),
        "reasoning_status": result.get("reasoning", {}).get("status"),
    }


def run_pipeline(
    branch: str = "all",
    date_from: str = "2026-01-01",
    date_to: str = "2026-03-31",
    dry_run: bool = False,
) -> dict[str, Any]:
    logger.info(
        "Starting AI pipeline | branch=%s | date_from=%s | date_to=%s | dry_run=%s",
        branch,
        date_from,
        date_to,
        dry_run,
    )

    if branch == "bi":
        bi = run_bi_interpretation(date_from=date_from, date_to=date_to, dry_run=dry_run)
        return {"success": True, "branch": branch, "bi": _compact_bi(bi)}

    if branch == "predict":
        forecast = run_forecasting(dry_run=dry_run)
        return {"success": True, "branch": branch, "forecast": _compact_forecast(forecast)}

    if branch in {"all", "report"}:
        report = run_report(date_from=date_from, date_to=date_to, dry_run=dry_run)
        return {"success": True, "branch": branch, "report": report}

    raise ValueError(f"Unsupported branch: {branch}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    try:
        result = run_pipeline(
            branch=args.branch,
            date_from=args.date_from,
            date_to=args.date_to,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        logger.exception("AI pipeline failed: %s", exc)
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc
    finally:
        close_bi_pool()
        close_forecast_pool()


if __name__ == "__main__":
    main()

