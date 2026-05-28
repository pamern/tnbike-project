"""Demand and color forecasting for TNBIKE."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ai.common import log_pending_issue, setup_logging


logger = setup_logging(__name__)
FUTURE_MONTHS = [(2026, 4), (2026, 5), (2026, 6)]


def _forecast_group_fallback(group_df: pd.DataFrame, group_code: str, group_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered = group_df.sort_values("period")
    last_revenue = float(ordered["revenue"].iloc[-1]) if not ordered.empty else 0.0
    rolling = float(ordered["revenue"].tail(3).mean()) if not ordered.empty else 0.0
    base = rolling if rolling > 0 else last_revenue
    avg_price = float(ordered["avg_unit_price"].replace(0, np.nan).dropna().tail(3).mean()) if not ordered.empty else 0.0
    avg_price = 0.0 if np.isnan(avg_price) else avg_price

    growth = 0.0
    if len(ordered) >= 4:
        prev = float(ordered["revenue"].iloc[-4:-1].mean())
        if prev > 0:
            growth = (base - prev) / prev
    growth = float(np.clip(growth, -0.15, 0.20))

    for idx, (year, month) in enumerate(FUTURE_MONTHS, start=1):
        forecast_revenue = max(base * ((1 + growth) ** idx), 0)
        ci_margin = max(0.12, min(0.30, abs(growth) + 0.12))
        rows.append(
            {
                "fiscal_year": year,
                "fiscal_month": month,
                "group_code": group_code,
                "group_name": group_name,
                "forecast_revenue": forecast_revenue,
                "forecast_qty": forecast_revenue / avg_price if avg_price > 0 else 0,
                "lower_revenue": forecast_revenue * (1 - ci_margin),
                "upper_revenue": forecast_revenue * (1 + ci_margin),
                "pessimistic_revenue": forecast_revenue * (1 - ci_margin),
                "base_revenue": forecast_revenue,
                "optimistic_revenue": forecast_revenue * (1 + ci_margin),
                "scenario_margin": ci_margin,
                "growth_assumption": growth,
                "method": "rolling_trend_fallback",
            }
        )
    return rows


def _summarize_forecast(
    forecast_df: pd.DataFrame,
    group_q2: pd.DataFrame,
    method_hint: list[str] | None = None,
) -> dict[str, Any]:
    total_revenue = float(group_q2["forecast_revenue_q2"].sum()) if not group_q2.empty else 0.0
    pessimistic = float(forecast_df["pessimistic_revenue"].sum()) if "pessimistic_revenue" in forecast_df else 0.0
    optimistic = float(forecast_df["optimistic_revenue"].sum()) if "optimistic_revenue" in forecast_df else 0.0
    methods = method_hint or (sorted(forecast_df["method"].dropna().unique().tolist()) if not forecast_df.empty else [])
    scenario_summary = {
        "pessimistic_revenue": pessimistic,
        "base_revenue": total_revenue,
        "optimistic_revenue": optimistic,
        "confidence_interval_explanation": (
            "Scenario range is derived from recent volatility, trend direction and model fallback uncertainty. "
            "Because the available monthly history is short, scenario planning is more reliable than a single-point forecast."
        ),
    }
    sensitivity = {
        "price_plus_5pct_revenue_delta": total_revenue * 0.05,
        "price_minus_5pct_revenue_delta": total_revenue * -0.05,
        "volume_plus_10pct_revenue_delta": total_revenue * 0.10,
        "volume_minus_10pct_revenue_delta": total_revenue * -0.10,
        "dealer_retention_plus_5pct_proxy": total_revenue * 0.05,
        "dealer_retention_minus_5pct_proxy": total_revenue * -0.05,
    }
    return {
        "q2_total_revenue_forecast": total_revenue,
        "top_groups": group_q2.head(5)["group_name"].tolist() if not group_q2.empty else [],
        "methods": methods,
        "key_drivers": [
            "Recent product-group revenue run-rate",
            "Observed Q1 seasonality versus prior-year Q1",
            "Average selling price by product group",
            "Dealer churn/retention exposure",
            "Color demand momentum as inventory velocity proxy",
        ],
    }, scenario_summary, sensitivity


def _build_demand_result(
    forecast_rows: list[dict[str, Any]],
    method_hint: list[str] | None = None,
) -> dict[str, Any]:
    forecast_df = pd.DataFrame(forecast_rows)
    group_q2 = (
        forecast_df.groupby(["group_code", "group_name"], dropna=False)
        .agg(
            forecast_revenue_q2=("forecast_revenue", "sum"),
            forecast_qty_q2=("forecast_qty", "sum"),
            lower_revenue_q2=("lower_revenue", "sum"),
            upper_revenue_q2=("upper_revenue", "sum"),
            pessimistic_revenue_q2=("pessimistic_revenue", "sum"),
            base_revenue_q2=("base_revenue", "sum"),
            optimistic_revenue_q2=("optimistic_revenue", "sum"),
        )
        .reset_index()
        .sort_values("forecast_revenue_q2", ascending=False)
    )
    summary, scenario_summary, sensitivity = _summarize_forecast(forecast_df, group_q2, method_hint)
    return {
        "status": "SUCCESS",
        "monthly_forecast": forecast_df.to_dict(orient="records"),
        "group_forecast": group_q2.to_dict(orient="records"),
        "summary": summary,
        "scenario_summary": scenario_summary,
        "sensitivity": sensitivity,
    }


def forecast_demand(monthly_group: pd.DataFrame) -> dict[str, Any]:
    if monthly_group.empty:
        reason = "No monthly group data available; demand forecast skipped."
        log_pending_issue(reason)
        return {"status": "NO_DATA", "group_forecast": [], "summary": {"warning": reason}}

    forecast_rows: list[dict[str, Any]] = []
    can_try_prophet = any(
        len(group_df) >= 8 and group_df["revenue"].sum() > 0
        for _, group_df in monthly_group.groupby(["group_code", "group_name"], dropna=False)
    )
    if not can_try_prophet:
        for (group_code, group_name), group_df in monthly_group.groupby(["group_code", "group_name"], dropna=False):
            forecast_rows.extend(_forecast_group_fallback(group_df, group_code, group_name))
        return _build_demand_result(forecast_rows, method_hint=["rolling_trend_fallback"])

    try:
        from prophet import Prophet

        for (group_code, group_name), group_df in monthly_group.groupby(["group_code", "group_name"], dropna=False):
            ordered = group_df.sort_values("period")
            if len(ordered) < 8 or ordered["revenue"].sum() <= 0:
                forecast_rows.extend(_forecast_group_fallback(ordered, group_code, group_name))
                continue

            prophet_df = ordered[["period", "revenue"]].rename(columns={"period": "ds", "revenue": "y"})
            model = Prophet(yearly_seasonality=False, weekly_seasonality=False, daily_seasonality=False)
            model.fit(prophet_df)
            future = pd.DataFrame({"ds": pd.to_datetime(["2026-04-01", "2026-05-01", "2026-06-01"])})
            pred = model.predict(future)
            avg_price = float(ordered["avg_unit_price"].replace(0, np.nan).dropna().tail(3).mean())
            avg_price = 0.0 if np.isnan(avg_price) else avg_price

            for _, row in pred.iterrows():
                forecast_revenue = max(float(row["yhat"]), 0.0)
                forecast_rows.append(
                    {
                        "fiscal_year": int(row["ds"].year),
                        "fiscal_month": int(row["ds"].month),
                        "group_code": group_code,
                        "group_name": group_name,
                        "forecast_revenue": forecast_revenue,
                        "forecast_qty": forecast_revenue / avg_price if avg_price > 0 else 0,
                        "lower_revenue": max(float(row["yhat_lower"]), 0.0),
                        "upper_revenue": max(float(row["yhat_upper"]), 0.0),
                        "pessimistic_revenue": max(float(row["yhat_lower"]), 0.0),
                        "base_revenue": forecast_revenue,
                        "optimistic_revenue": max(float(row["yhat_upper"]), 0.0),
                        "scenario_margin": (
                            (max(float(row["yhat_upper"]), 0.0) - max(float(row["yhat_lower"]), 0.0)) / (2 * forecast_revenue)
                            if forecast_revenue > 0 else 0
                        ),
                        "growth_assumption": 0,
                        "method": "prophet",
                    }
                )
    except Exception as exc:
        reason = f"Prophet forecast failed; using rolling fallback. Error: {exc}"
        logger.warning(reason)
        log_pending_issue(reason)
        forecast_rows = []
        for (group_code, group_name), group_df in monthly_group.groupby(["group_code", "group_name"], dropna=False):
            forecast_rows.extend(_forecast_group_fallback(group_df, group_code, group_name))

    return _build_demand_result(forecast_rows)


def forecast_colors(color_monthly: pd.DataFrame, top_n: int = 10) -> dict[str, Any]:
    if color_monthly.empty:
        return {"status": "NO_DATA", "rising_colors": [], "declining_colors": [], "top_colors": []}

    latest_period = color_monthly["period"].max()
    recent_start = latest_period - pd.DateOffset(months=2)
    previous_start = latest_period - pd.DateOffset(months=5)
    same_period_last_year_start = recent_start - pd.DateOffset(years=1)
    same_period_last_year_end = latest_period - pd.DateOffset(years=1)
    recent = (
        color_monthly[color_monthly["period"] >= recent_start]
        .groupby("color", dropna=False)["revenue"]
        .sum()
    )
    previous_window = (
        color_monthly[(color_monthly["period"] >= previous_start) & (color_monthly["period"] < recent_start)]
        .groupby("color", dropna=False)["revenue"]
        .sum()
    )
    same_period_last_year = (
        color_monthly[
            (color_monthly["period"] >= same_period_last_year_start)
            & (color_monthly["period"] <= same_period_last_year_end)
        ]
        .groupby("color", dropna=False)["revenue"]
        .sum()
    )
    previous = same_period_last_year if float(previous_window.sum()) == 0 else previous_window
    trend = pd.DataFrame({"recent_revenue": recent, "previous_revenue": previous}).fillna(0).reset_index()
    trend["trend_delta"] = trend["recent_revenue"] - trend["previous_revenue"]
    trend["trend_pct"] = np.where(
        trend["previous_revenue"] > 0,
        trend["trend_delta"] / trend["previous_revenue"],
        np.where(trend["recent_revenue"] > 0, 1.0, 0.0),
    )
    top_colors = trend.sort_values("recent_revenue", ascending=False).head(top_n)
    rising = trend[trend["recent_revenue"] > 0].sort_values(["trend_delta", "recent_revenue"], ascending=False).head(top_n)
    declining = trend[trend["previous_revenue"] > 0].sort_values(["trend_delta", "recent_revenue"], ascending=True).head(top_n)
    return {
        "status": "SUCCESS",
        "top_colors": top_colors.to_dict(orient="records"),
        "rising_colors": rising.to_dict(orient="records"),
        "declining_colors": declining.to_dict(orient="records"),
    }


def run_demand_forecast(monthly_group: pd.DataFrame, color_monthly: pd.DataFrame) -> dict[str, Any]:
    return {
        "demand": forecast_demand(monthly_group),
        "colors": forecast_colors(color_monthly),
    }
