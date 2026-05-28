"""BI interpreter package."""

from __future__ import annotations

from datetime import date
from typing import Any

from ai.interpreters.bi_context_builder import build_context_package
from ai.interpreters.bi_data_extractor import close_db_pool, extract_bi_data
from ai.interpreters.bi_llm_interpreter import interpret_bi_context


def run_bi_interpretation(
    date_from: str | date,
    date_to: str | date,
    dry_run: bool = False,
) -> dict[str, Any]:
    extracted = extract_bi_data(date_from, date_to)
    context_package = build_context_package(extracted)
    interpretation = interpret_bi_context(
        baseline_insights=context_package["baseline_insights"],
        data_context=context_package["data_context"],
        dry_run=dry_run,
    )

    return {
        "date_from": str(date_from),
        "date_to": str(date_to),
        "dry_run": dry_run,
        "data_summary": {
            name: {
                "rows": int(len(df)),
                "columns": list(df.columns),
            }
            for name, df in extracted.items()
        },
        "context": context_package,
        "interpretation": interpretation,
    }


__all__ = ["run_bi_interpretation", "close_db_pool"]

