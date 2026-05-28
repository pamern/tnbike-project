"""Forecasting package."""

from __future__ import annotations

from typing import Any

from ai.forecasting.churn_predictor import predict_churn
from ai.forecasting.demand_forecaster import run_demand_forecast
from ai.forecasting.feature_engineering import build_forecasting_features, close_db_pool
from ai.forecasting.llm_reasoner import reason_over_forecasts


def run_forecasting(dry_run: bool = False) -> dict[str, Any]:
    features = build_forecasting_features()
    forecast_results = run_demand_forecast(
        monthly_group=features["monthly_group"],
        color_monthly=features["color_monthly"],
    )
    churn_results = predict_churn(features["customer_features"])
    reasoning = reason_over_forecasts(
        forecast_results=forecast_results,
        churn_results=churn_results,
        dry_run=dry_run,
    )
    return {
        "dry_run": dry_run,
        "feature_summary": {
            name: {"rows": int(len(df)), "columns": list(df.columns)}
            for name, df in features.items()
        },
        "forecast_results": forecast_results,
        "churn_results": churn_results,
        "reasoning": reasoning,
    }


__all__ = ["run_forecasting", "close_db_pool"]

