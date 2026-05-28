"""Entrypoint for TNBIKE AI report generation."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import pandas as pd

from ai.forecasting import close_db_pool as close_forecast_pool
from ai.forecasting import run_forecasting
from ai.interpreters import close_db_pool as close_bi_pool
from ai.interpreters import run_bi_interpretation
from ai.report.report_generator import generate_report
from ai.report.strategic_intelligence import generate_strategic_intelligence


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))


def _attach_bi_data(bi_result: dict[str, Any]) -> dict[str, Any]:
    data = {}
    extracted = bi_result.pop("_extracted_data", None)
    if extracted:
        data = {name: _records(df) for name, df in extracted.items()}
    bi_result["data"] = data
    return bi_result


def run_report(
    date_from: str = "2026-01-01",
    date_to: str = "2026-03-31",
    dry_run: bool = False,
) -> dict[str, Any]:
    from ai.interpreters.bi_context_builder import build_context_package
    from ai.interpreters.bi_data_extractor import extract_bi_data
    from ai.interpreters.bi_llm_interpreter import interpret_bi_context

    extracted = extract_bi_data(date_from, date_to)
    context_package = build_context_package(extracted)
    # Strategic report generation uses its own multi-step synthesis. Keep the
    # legacy BI LLM dry in report mode to avoid burning quota on duplicate narratives.
    interpretation = interpret_bi_context(
        baseline_insights=context_package["baseline_insights"],
        data_context=context_package["data_context"],
        dry_run=True,
    )
    bi_result = {
        "date_from": date_from,
        "date_to": date_to,
        "dry_run": dry_run,
        "context": context_package,
        "interpretation": interpretation,
        "_extracted_data": extracted,
    }
    bi_result = _attach_bi_data(bi_result)

    forecast_result = run_forecasting(dry_run=True)
    strategic_result = generate_strategic_intelligence(
        bi_result=bi_result,
        forecast_result=forecast_result,
        dry_run=dry_run,
    )
    paths = generate_report(
        bi_result=bi_result,
        forecast_result=forecast_result,
        strategic_result=strategic_result,
    )
    return {
        "success": True,
        "dry_run": dry_run,
        "paths": paths,
        "bi_status": interpretation.get("status", "UNKNOWN"),
        "forecast_status": forecast_result.get("forecast_results", {}).get("demand", {}).get("status", "UNKNOWN"),
        "reasoning_status": forecast_result.get("reasoning", {}).get("status", "UNKNOWN"),
        "strategic_status": strategic_result.get("status", "UNKNOWN"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate TNBIKE AI report")
    parser.add_argument("--date-from", default="2026-01-01")
    parser.add_argument("--date-to", default="2026-03-31")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    try:
        result = run_report(
            date_from=args.date_from,
            date_to=args.date_to,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        close_bi_pool()
        close_forecast_pool()


if __name__ == "__main__":
    main()
