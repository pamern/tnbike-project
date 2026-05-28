"""BI data extraction queries for TNBIKE."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from ai.common import ensure_project_on_path, load_environment


load_environment()
ensure_project_on_path()

from src.database.connection import close_connection_pool, get_cursor  # noqa: E402


def _read_dataframe(query: str, params: tuple[Any, ...]) -> pd.DataFrame:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def _date_filter() -> str:
    return "order_date >= %s AND order_date <= %s"


def get_revenue_trend(date_from: str | date, date_to: str | date) -> pd.DataFrame:
    query = f"""
        SELECT
            fiscal_year,
            fiscal_month,
            COUNT(DISTINCT so_number) AS order_count,
            COUNT(DISTINCT customer_code) AS active_customer_count,
            SUM(quantity)::float AS total_qty,
            SUM(line_total)::float AS total_revenue,
            AVG(unit_price)::float AS avg_unit_price
        FROM tnbike.fact_sales
        WHERE {_date_filter()}
        GROUP BY fiscal_year, fiscal_month
        ORDER BY fiscal_year, fiscal_month;
    """
    return _read_dataframe(query, (date_from, date_to))


def get_product_analysis(date_from: str | date, date_to: str | date) -> pd.DataFrame:
    query = f"""
        SELECT
            COALESCE(group_code, 'UNKNOWN') AS group_code,
            COALESCE(group_name, 'Unknown') AS group_name,
            COUNT(DISTINCT product_code) AS sku_count,
            COUNT(DISTINCT so_number) AS order_count,
            SUM(quantity)::float AS total_qty,
            SUM(line_total)::float AS total_revenue,
            AVG(unit_price)::float AS avg_unit_price,
            SUM(line_total)::float / NULLIF(SUM(SUM(line_total)) OVER (), 0)::float AS revenue_share
        FROM tnbike.fact_sales
        WHERE {_date_filter()}
        GROUP BY group_code, group_name
        ORDER BY total_revenue DESC;
    """
    return _read_dataframe(query, (date_from, date_to))


def get_customer_rfm(date_from: str | date, date_to: str | date) -> pd.DataFrame:
    query = f"""
        WITH customer_period AS (
            SELECT
                customer_code,
                MAX(customer_name) AS customer_name,
                MAX(province_name) AS province_name,
                MAX(region) AS region,
                COUNT(DISTINCT so_number) AS frequency,
                SUM(line_total)::float AS monetary,
                MAX(order_date) AS last_order_date,
                MIN(order_date) AS first_order_date
            FROM tnbike.fact_sales
            WHERE {_date_filter()}
            GROUP BY customer_code
        ),
        scored AS (
            SELECT
                *,
                (%s::date - last_order_date)::int AS recency_days,
                NTILE(5) OVER (ORDER BY (%s::date - last_order_date) DESC) AS recency_score,
                NTILE(5) OVER (ORDER BY frequency ASC) AS frequency_score,
                NTILE(5) OVER (ORDER BY monetary ASC) AS monetary_score
            FROM customer_period
        )
        SELECT
            customer_code,
            customer_name,
            province_name,
            region,
            frequency,
            monetary,
            last_order_date,
            recency_days,
            recency_score,
            frequency_score,
            monetary_score,
            (recency_score + frequency_score + monetary_score) AS rfm_score
        FROM scored
        ORDER BY rfm_score DESC, monetary DESC
        LIMIT 100;
    """
    return _read_dataframe(query, (date_from, date_to, date_to, date_to))


def get_geo_analysis(date_from: str | date, date_to: str | date) -> pd.DataFrame:
    query = f"""
        SELECT
            COALESCE(region, 'Unknown') AS region,
            COALESCE(province_name, 'Unknown') AS province_name,
            COUNT(DISTINCT customer_code) AS active_customer_count,
            COUNT(DISTINCT so_number) AS order_count,
            SUM(quantity)::float AS total_qty,
            SUM(line_total)::float AS total_revenue,
            SUM(line_total)::float / NULLIF(SUM(SUM(line_total)) OVER (), 0)::float AS revenue_share
        FROM tnbike.fact_sales
        WHERE {_date_filter()}
        GROUP BY region, province_name
        ORDER BY total_revenue DESC
        LIMIT 50;
    """
    return _read_dataframe(query, (date_from, date_to))


def get_operational_kpis(date_from: str | date, date_to: str | date) -> pd.DataFrame:
    query = f"""
        SELECT
            COUNT(DISTINCT so_number) AS order_count,
            COUNT(DISTINCT customer_code) AS active_customer_count,
            COUNT(DISTINCT product_code) AS active_sku_count,
            SUM(quantity)::float AS total_qty,
            SUM(line_total)::float AS total_revenue,
            SUM(line_total)::float / NULLIF(COUNT(DISTINCT so_number), 0)::float AS avg_order_value,
            SUM(quantity)::float / NULLIF(COUNT(DISTINCT so_number), 0)::float AS avg_qty_per_order,
            COUNT(*)::float / NULLIF(COUNT(DISTINCT so_number), 0)::float AS avg_lines_per_order,
            MIN(order_date) AS first_order_date,
            MAX(order_date) AS last_order_date
        FROM tnbike.fact_sales
        WHERE {_date_filter()};
    """
    return _read_dataframe(query, (date_from, date_to))


def extract_bi_data(date_from: str | date, date_to: str | date) -> dict[str, pd.DataFrame]:
    return {
        "revenue_trend": get_revenue_trend(date_from, date_to),
        "product_analysis": get_product_analysis(date_from, date_to),
        "customer_rfm": get_customer_rfm(date_from, date_to),
        "geo_analysis": get_geo_analysis(date_from, date_to),
        "operational_kpis": get_operational_kpis(date_from, date_to),
    }


def close_db_pool() -> None:
    close_connection_pool()

