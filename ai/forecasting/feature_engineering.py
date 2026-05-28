"""Feature engineering for TNBIKE predictive analytics."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ai.common import ensure_project_on_path, load_environment


load_environment()
ensure_project_on_path()

from src.database.connection import close_connection_pool, get_cursor  # noqa: E402


def _read_dataframe(query: str, params: tuple[Any, ...] | None = None) -> pd.DataFrame:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def load_sales_fact() -> pd.DataFrame:
    query = """
        SELECT
            order_date,
            fiscal_year,
            fiscal_month,
            so_number,
            customer_code,
            customer_name,
            province_name,
            region,
            product_code,
            product_name,
            color,
            line_name,
            group_code,
            group_name,
            quantity::float AS quantity,
            unit_price::float AS unit_price,
            line_total::float AS line_total
        FROM tnbike.fact_sales
        WHERE order_date IS NOT NULL;
    """
    df = _read_dataframe(query)
    if not df.empty:
        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    return df


def build_monthly_group_features(df_fact: pd.DataFrame) -> pd.DataFrame:
    if df_fact.empty:
        return pd.DataFrame()

    df = df_fact.copy()
    monthly = (
        df.groupby(["fiscal_year", "fiscal_month", "group_code", "group_name"], dropna=False)
        .agg(
            qty=("quantity", "sum"),
            revenue=("line_total", "sum"),
            order_count=("so_number", "nunique"),
            active_customer_count=("customer_code", "nunique"),
            avg_unit_price=("unit_price", "mean"),
        )
        .reset_index()
    )
    monthly["period"] = pd.to_datetime(
        monthly["fiscal_year"].astype(str) + "-" + monthly["fiscal_month"].astype(str).str.zfill(2) + "-01"
    )
    monthly = monthly.sort_values(["group_code", "period"]).reset_index(drop=True)

    for col in ["qty", "revenue"]:
        monthly[f"{col}_lag_1"] = monthly.groupby("group_code")[col].shift(1)
        monthly[f"{col}_lag_2"] = monthly.groupby("group_code")[col].shift(2)
        monthly[f"{col}_rolling_3"] = (
            monthly.groupby("group_code")[col]
            .transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
        )

    monthly["month_sin"] = np.sin(2 * np.pi * monthly["fiscal_month"].astype(float) / 12)
    monthly["month_cos"] = np.cos(2 * np.pi * monthly["fiscal_month"].astype(float) / 12)
    for col in ["group_code", "group_name"]:
        monthly[col] = monthly[col].fillna("Unknown")
    numeric_cols = monthly.select_dtypes(include=["number"]).columns
    monthly[numeric_cols] = monthly[numeric_cols].replace([np.inf, -np.inf], np.nan).fillna(0)
    return monthly


def build_color_features(df_fact: pd.DataFrame) -> pd.DataFrame:
    if df_fact.empty:
        return pd.DataFrame()

    color = (
        df_fact.groupby(["fiscal_year", "fiscal_month", "color"], dropna=False)
        .agg(
            qty=("quantity", "sum"),
            revenue=("line_total", "sum"),
            order_count=("so_number", "nunique"),
        )
        .reset_index()
    )
    color["color"] = color["color"].fillna("Unknown")
    color["period"] = pd.to_datetime(
        color["fiscal_year"].astype(str) + "-" + color["fiscal_month"].astype(str).str.zfill(2) + "-01"
    )
    color = color.sort_values(["color", "period"]).reset_index(drop=True)
    color["revenue_lag_1"] = color.groupby("color")["revenue"].shift(1).fillna(0)
    color["revenue_rolling_3"] = (
        color.groupby("color")["revenue"]
        .transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
        .fillna(0)
    )
    return color


def build_customer_features(df_fact: pd.DataFrame) -> pd.DataFrame:
    if df_fact.empty:
        return pd.DataFrame()

    orders = (
        df_fact.groupby(["customer_code", "so_number"], dropna=False)
        .agg(
            order_date=("order_date", "first"),
            customer_name=("customer_name", "first"),
            province_name=("province_name", "first"),
            region=("region", "first"),
            order_revenue=("line_total", "sum"),
        )
        .reset_index()
    )
    snapshot_date = orders["order_date"].max()
    cutoff_recent = snapshot_date - pd.Timedelta(days=90)
    cutoff_previous = snapshot_date - pd.Timedelta(days=180)

    base = (
        orders.groupby("customer_code", dropna=False)
        .agg(
            customer_name=("customer_name", "first"),
            province_name=("province_name", "first"),
            region=("region", "first"),
            first_order_date=("order_date", "min"),
            last_order_date=("order_date", "max"),
            order_frequency=("so_number", "nunique"),
            total_revenue=("order_revenue", "sum"),
            avg_order_value=("order_revenue", "mean"),
        )
        .reset_index()
    )
    recent = (
        orders[orders["order_date"] > cutoff_recent]
        .groupby("customer_code")
        .agg(recent_90d_revenue=("order_revenue", "sum"), recent_90d_orders=("so_number", "nunique"))
        .reset_index()
    )
    previous = (
        orders[(orders["order_date"] > cutoff_previous) & (orders["order_date"] <= cutoff_recent)]
        .groupby("customer_code")
        .agg(previous_90d_revenue=("order_revenue", "sum"))
        .reset_index()
    )
    base = base.merge(recent, on="customer_code", how="left").merge(previous, on="customer_code", how="left")
    base[["recent_90d_revenue", "recent_90d_orders", "previous_90d_revenue"]] = base[
        ["recent_90d_revenue", "recent_90d_orders", "previous_90d_revenue"]
    ].fillna(0)
    base["days_since_last_order"] = (snapshot_date - base["last_order_date"]).dt.days
    base["revenue_trend"] = np.where(
        base["previous_90d_revenue"] > 0,
        (base["recent_90d_revenue"] - base["previous_90d_revenue"]) / base["previous_90d_revenue"],
        np.where(base["recent_90d_revenue"] > 0, 1.0, 0.0),
    )
    base["recency_score"] = pd.qcut(base["days_since_last_order"].rank(method="first"), 5, labels=False) + 1
    base["frequency_score"] = pd.qcut(base["order_frequency"].rank(method="first"), 5, labels=False) + 1
    base["monetary_score"] = pd.qcut(base["total_revenue"].rank(method="first"), 5, labels=False) + 1
    base["rfm_score"] = 6 - base["recency_score"] + base["frequency_score"] + base["monetary_score"]
    base["snapshot_date"] = snapshot_date
    return base.replace([np.inf, -np.inf], np.nan).fillna(0)


def build_forecasting_features() -> dict[str, pd.DataFrame]:
    fact = load_sales_fact()
    return {
        "fact_sales": fact,
        "monthly_group": build_monthly_group_features(fact),
        "color_monthly": build_color_features(fact),
        "customer_features": build_customer_features(fact),
    }


def close_db_pool() -> None:
    close_connection_pool()
