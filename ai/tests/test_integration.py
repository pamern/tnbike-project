"""Integration tests for the TNBIKE AI layer.

These tests avoid real LLM calls by using dry-run paths.
"""

from __future__ import annotations

from ai.forecasting import close_db_pool as close_forecast_pool
from ai.forecasting import run_forecasting
from ai.interpreters import close_db_pool as close_bi_pool
from ai.interpreters import run_bi_interpretation
from ai.interpreters.bi_data_extractor import get_operational_kpis
from ai.report.report_runner import run_report


def test_db_connection_has_fact_sales_rows() -> None:
    df = get_operational_kpis("2026-01-01", "2026-03-31")
    try:
        assert not df.empty
        assert int(df.iloc[0]["order_count"]) > 0
    finally:
        close_bi_pool()


def test_bi_interpreter_dry_run_returns_data_summary() -> None:
    result = run_bi_interpretation("2026-01-01", "2026-03-31", dry_run=True)
    try:
        assert result["interpretation"]["status"] == "DRY_RUN"
        assert result["data_summary"]["operational_kpis"]["rows"] == 1
        assert result["data_summary"]["product_analysis"]["rows"] > 0
    finally:
        close_bi_pool()


def test_forecasting_dry_run_returns_forecast_and_churn() -> None:
    result = run_forecasting(dry_run=True)
    try:
        assert result["forecast_results"]["demand"]["status"] == "SUCCESS"
        assert result["forecast_results"]["demand"]["summary"]["q2_total_revenue_forecast"] > 0
        assert result["churn_results"]["summary"]["customer_count"] > 0
        assert result["reasoning"]["status"] == "LLM_UNAVAILABLE"
    finally:
        close_forecast_pool()


def test_report_runner_dry_run_creates_files() -> None:
    result = run_report(dry_run=True)
    try:
        assert result["success"] is True
        assert result["paths"]["html"].endswith(".html")
        assert result["paths"]["markdown"].endswith(".md")
    finally:
        close_bi_pool()
        close_forecast_pool()

