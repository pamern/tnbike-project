"""
forecasting_revenue.py

Code forecast Q2/2026 cho `src/analytics/forecasting_revenue.py`.

Mục tiêu:
1. Không tạo dòng tháng tương lai với qty = 0 để predict chính tháng đó.
2. Dự báo T4 bằng trạng thái T3/2026, T5 bằng trạng thái forecast T4, T6 bằng trạng thái forecast T5.
3. Nếu Random Forest kém baseline, dùng baseline tốt hơn làm fallback.
4. Giữ benchmark gọn và ổn định (Naive, Rolling 2/3 months, EWMA).
5. Calibration theo nhóm dựa trên forecast bias, có giới hạn hệ số.
6. Xuất forecast theo tháng, nhóm sản phẩm, SKU và top 20 SKU.

Cách dùng trong notebook:
- Chạy xong các cell tạo `df_model_input` và `df_train_ready`.
- Dán/chạy toàn bộ file này trong một cell hoặc `%run src/analytics/forecasting_revenue.py`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

CSV_ENCODING = "utf-8-sig"
INPUT_SUBDIR = Path("data") / "processed" / "forecast" / "input"
OUTPUT_SUBDIR = Path("data") / "processed" / "forecast" / "results" / "sku_monthly_q2_2026"

from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor, GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score



# ============================================================
# 0. Load / prepare required dataframes
# ============================================================

PREP_CATEGORICAL_FEATURES = [
    "product_code",
    "group_code",
    "group_name",
    "line_name",
    "color",
]

PREP_NUMERIC_FEATURES = [
    "fiscal_year",
    "fiscal_month",
    "qty",
    "revenue",
    "order_count",
    "active_dealer_count",
    "avg_unit_price",
    "qty_lag_1",
    "qty_lag_2",
    "revenue_lag_1",
    "revenue_lag_2",
    "rolling_2m_qty",
    "rolling_3m_qty",
    "mom_qty_growth",
    "mom_revenue_growth",
    "group_month_qty",
    "group_month_revenue",
    "sku_qty_share_in_group",
    "sku_revenue_share_in_group",
    "same_month_qty_last_year",
    "same_month_revenue_last_year",
    "sku_yoy_qty_growth",
    "sku_yoy_revenue_growth",
    "has_sku_yoy",
    "is_new_sku",
    "missing_master_flag",
    "missing_group_flag",
    "missing_line_flag",
    "missing_color_flag",
    "numeric_issue_flag",
    "outlier_flag",
]


def _resolve_project_root() -> Path:
    """Tìm project root khi chạy bằng `py -m src.analytics.forecasting_revenue`."""
    try:
        return Path(project_root)
    except NameError:
        root = Path.cwd()
        if not (root / "data").exists() and (root.parent / "data").exists():
            root = root.parent
        return root


def _read_fact_sales_from_postgres() -> pd.DataFrame:
    """
    Đọc dữ liệu từ PostgreSQL bảng tnbike.fact_sales.

    Cần file .env ở project root có các biến:
    PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD.
    """
    try:
        from dotenv import load_dotenv
        import psycopg2
    except ImportError as exc:
        raise ImportError(
            "Thiếu thư viện để đọc PostgreSQL. Cài thêm: pip install python-dotenv psycopg2-binary"
        ) from exc

    import os

    root = _resolve_project_root()
    load_dotenv(root / ".env")

    conn_kwargs = {
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": os.getenv("PGDATABASE", "tnbike_db"),
        "user": os.getenv("PGUSER", "postgres"),
        "password": os.getenv("PGPASSWORD", "postgres"),
    }

    query = """
        SELECT
            order_date,
            fiscal_year,
            fiscal_month,
            so_number,
            customer_code,
            product_code,
            product_name,
            color,
            line_name,
            group_code,
            group_name,
            quantity,
            unit_price,
            line_total
        FROM tnbike.fact_sales
        WHERE order_date IS NOT NULL
    """

    with psycopg2.connect(**conn_kwargs) as conn:
        return pd.read_sql_query(query, conn)


def _rebuild_features_for_prepared_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """Tạo lại lag, rolling, tỷ trọng nhóm và YoY cho panel SKU-tháng."""
    panel = panel.copy()
    panel = panel.sort_values(["product_code", "fiscal_year", "fiscal_month"])

    # Lag theo SKU
    panel["qty_lag_1"] = panel.groupby("product_code")["qty"].shift(1)
    panel["qty_lag_2"] = panel.groupby("product_code")["qty"].shift(2)
    panel["revenue_lag_1"] = panel.groupby("product_code")["revenue"].shift(1)
    panel["revenue_lag_2"] = panel.groupby("product_code")["revenue"].shift(2)

    # Rolling chỉ dùng tháng quá khứ, không dùng tháng hiện tại để tránh leakage
    panel["rolling_2m_qty"] = (
        panel.groupby("product_code")["qty"]
        .transform(lambda s: s.shift(1).rolling(window=2, min_periods=1).mean())
    )
    panel["rolling_3m_qty"] = (
        panel.groupby("product_code")["qty"]
        .transform(lambda s: s.shift(1).rolling(window=3, min_periods=1).mean())
    )

    panel["mom_qty_growth"] = np.where(
        panel["qty_lag_1"] > 0,
        (panel["qty"] - panel["qty_lag_1"]) / panel["qty_lag_1"],
        0,
    )
    panel["mom_revenue_growth"] = np.where(
        panel["revenue_lag_1"] > 0,
        (panel["revenue"] - panel["revenue_lag_1"]) / panel["revenue_lag_1"],
        0,
    )

    panel = panel.drop(columns=["group_month_qty", "group_month_revenue"], errors="ignore")
    group_month_total = (
        panel.groupby(["fiscal_year", "fiscal_month", "group_name"], dropna=False)
        .agg(
            group_month_qty=("qty", "sum"),
            group_month_revenue=("revenue", "sum"),
        )
        .reset_index()
    )
    panel = panel.merge(
        group_month_total,
        on=["fiscal_year", "fiscal_month", "group_name"],
        how="left",
    )

    panel["sku_qty_share_in_group"] = np.where(
        panel["group_month_qty"] > 0,
        panel["qty"] / panel["group_month_qty"],
        0,
    )
    panel["sku_revenue_share_in_group"] = np.where(
        panel["group_month_revenue"] > 0,
        panel["revenue"] / panel["group_month_revenue"],
        0,
    )

    last_year = panel[["product_code", "fiscal_year", "fiscal_month", "qty", "revenue"]].copy()
    last_year["fiscal_year"] = last_year["fiscal_year"] + 1
    last_year = last_year.rename(
        columns={
            "qty": "same_month_qty_last_year",
            "revenue": "same_month_revenue_last_year",
        }
    )
    panel = panel.drop(
        columns=["same_month_qty_last_year", "same_month_revenue_last_year"],
        errors="ignore",
    )
    panel = panel.merge(
        last_year,
        on=["product_code", "fiscal_year", "fiscal_month"],
        how="left",
    )

    panel["same_month_qty_last_year"] = panel["same_month_qty_last_year"].fillna(0)
    panel["same_month_revenue_last_year"] = panel["same_month_revenue_last_year"].fillna(0)
    panel["has_sku_yoy"] = (panel["same_month_qty_last_year"] > 0).astype(int)
    panel["sku_yoy_qty_growth"] = np.where(
        panel["same_month_qty_last_year"] > 0,
        (panel["qty"] - panel["same_month_qty_last_year"]) / panel["same_month_qty_last_year"],
        0,
    )
    panel["sku_yoy_revenue_growth"] = np.where(
        panel["same_month_revenue_last_year"] > 0,
        (panel["revenue"] - panel["same_month_revenue_last_year"]) / panel["same_month_revenue_last_year"],
        0,
    )

    numeric_cols = list(dict.fromkeys(PREP_NUMERIC_FEATURES + ["qty_next_month"]))
    for col in numeric_cols:
        if col not in panel.columns:
            panel[col] = 0
        panel[col] = pd.to_numeric(panel[col], errors="coerce")

    panel[numeric_cols] = panel[numeric_cols].replace([np.inf, -np.inf], np.nan).fillna(0)
    return panel


def _prepare_forecast_input_from_fact_sales() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Chuẩn bị df_model_input và df_train_ready trực tiếp từ fact_sales.

    Hàm này chỉ tạo dataframe trong bộ nhớ.
    File input CSV được xuất sau bước split, chỉ gồm:
    - train_sku.csv
    - test_sku.csv
    """
    root = _resolve_project_root()
    input_dir = root / INPUT_SUBDIR
    input_dir.mkdir(parents=True, exist_ok=True)

    fact = _read_fact_sales_from_postgres()
    if fact.empty:
        raise ValueError("Bảng tnbike.fact_sales đang rỗng, không thể chuẩn bị dữ liệu forecast.")

    fact = fact.copy()
    fact["order_date"] = pd.to_datetime(fact["order_date"], errors="coerce")
    fact["fiscal_year"] = pd.to_numeric(fact["fiscal_year"], errors="coerce").astype("Int64")
    fact["fiscal_month"] = pd.to_numeric(fact["fiscal_month"], errors="coerce").astype("Int64")
    fact["product_code"] = fact["product_code"].astype("string")

    for col in ["quantity", "unit_price", "line_total"]:
        fact[col] = pd.to_numeric(fact[col], errors="coerce").fillna(0)

    dim_cols = ["product_code", "product_name", "color", "line_name", "group_code", "group_name"]
    product_dim = (
        fact.sort_values("order_date")
        .groupby("product_code", as_index=False)[dim_cols[1:]]
        .last()
    )

    months = (
        fact[["fiscal_year", "fiscal_month"]]
        .dropna()
        .drop_duplicates()
        .sort_values(["fiscal_year", "fiscal_month"])
        .reset_index(drop=True)
    )
    products = product_dim[["product_code"]].drop_duplicates().reset_index(drop=True)

    # Cross join để mỗi SKU có đủ dòng theo từng tháng quan sát được.
    months["_key"] = 1
    products["_key"] = 1
    panel = products.merge(months, on="_key", how="outer").drop(columns="_key")
    panel = panel.merge(product_dim, on="product_code", how="left")

    monthly = (
        fact.groupby(["fiscal_year", "fiscal_month", "product_code"], dropna=False)
        .agg(
            qty=("quantity", "sum"),
            revenue=("line_total", "sum"),
            order_count=("so_number", "nunique"),
            active_dealer_count=("customer_code", "nunique"),
            observed_avg_unit_price=("unit_price", "mean"),
        )
        .reset_index()
    )

    panel = panel.merge(monthly, on=["fiscal_year", "fiscal_month", "product_code"], how="left")
    for col in ["qty", "revenue", "order_count", "active_dealer_count", "observed_avg_unit_price"]:
        panel[col] = pd.to_numeric(panel[col], errors="coerce").fillna(0)

    product_price_fallback = (
        fact[fact["unit_price"] > 0]
        .groupby("product_code", as_index=False)
        .agg(product_avg_unit_price=("unit_price", "median"))
    )
    panel = panel.merge(product_price_fallback, on="product_code", how="left")
    global_price = float(fact.loc[fact["unit_price"] > 0, "unit_price"].median()) if (fact["unit_price"] > 0).any() else 0
    panel["avg_unit_price"] = np.where(
        panel["qty"] > 0,
        panel["revenue"] / panel["qty"].replace(0, np.nan),
        panel["product_avg_unit_price"],
    )
    panel["avg_unit_price"] = pd.to_numeric(panel["avg_unit_price"], errors="coerce").fillna(global_price).fillna(0)
    panel = panel.drop(columns=["observed_avg_unit_price", "product_avg_unit_price"], errors="ignore")

    for col in PREP_CATEGORICAL_FEATURES:
        panel[col] = panel[col].astype("string").fillna("Unknown")
        panel[col] = panel[col].replace({"": "Unknown", "<NA>": "Unknown"})

    panel["missing_group_flag"] = panel["group_name"].isin(["Unknown", "", pd.NA]).astype(int)
    panel["missing_line_flag"] = panel["line_name"].isin(["Unknown", "", pd.NA]).astype(int)
    panel["missing_color_flag"] = panel["color"].isin(["Unknown", "", pd.NA]).astype(int)
    panel["missing_master_flag"] = (
        (panel["missing_group_flag"] == 1)
        | (panel["missing_line_flag"] == 1)
        | (panel["missing_color_flag"] == 1)
    ).astype(int)
    panel["numeric_issue_flag"] = ((panel["qty"] < 0) | (panel["revenue"] < 0) | (panel["avg_unit_price"] < 0)).astype(int)

    first_sale = (
        fact[fact["quantity"] > 0]
        .groupby("product_code", as_index=False)
        .agg(first_sale_date=("order_date", "min"))
    )
    panel = panel.merge(first_sale, on="product_code", how="left")
    panel["is_new_sku"] = (panel["first_sale_date"].dt.year >= 2026).astype(int)
    panel = panel.drop(columns=["first_sale_date"], errors="ignore")

    # Outlier đơn giản theo SKU, chỉ đánh dấu để model biết tháng bất thường; không xoá dữ liệu.
    panel["outlier_flag"] = 0
    for _, idx in panel.groupby("product_code").groups.items():
        qty = panel.loc[idx, "qty"].astype(float)
        if len(qty) >= 4:
            q1, q3 = qty.quantile(0.25), qty.quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                panel.loc[idx, "outlier_flag"] = ((qty < q1 - 1.5 * iqr) | (qty > q3 + 1.5 * iqr)).astype(int)

    panel["year_month"] = panel["fiscal_year"].astype(str) + "-" + panel["fiscal_month"].astype(str).str.zfill(2)
    panel = _rebuild_features_for_prepared_panel(panel)

    panel = panel.sort_values(["product_code", "fiscal_year", "fiscal_month"])
    panel["qty_next_month"] = panel.groupby("product_code")["qty"].shift(-1)

    # Chỉ train/backtest trên các dòng có tháng kế tiếp thật sự trong dữ liệu.
    train_ready = panel[panel["qty_next_month"].notna()].copy()

    # Chuẩn hoá cột lần cuối để tránh thiếu feature.
    for col in PREP_CATEGORICAL_FEATURES:
        panel[col] = panel[col].astype("string").fillna("Unknown")
        train_ready[col] = train_ready[col].astype("string").fillna("Unknown")
    for col in PREP_NUMERIC_FEATURES + ["qty_next_month"]:
        if col not in panel.columns:
            panel[col] = 0
        if col not in train_ready.columns:
            train_ready[col] = 0
        panel[col] = pd.to_numeric(panel[col], errors="coerce").fillna(0)
        train_ready[col] = pd.to_numeric(train_ready[col], errors="coerce").fillna(0)

    print("Prepared forecast input from PostgreSQL fact_sales:")
    print(f"- df_model_input: {panel.shape}")
    print(f"- df_train_ready: {train_ready.shape}")
    print("Input CSV export is limited to train_sku.csv and test_sku.csv after train/test split.")

    return panel, train_ready


def _auto_load_required_dataframes_if_needed():
    """
    Ưu tiên dùng dataframe có sẵn trong notebook.
    Nếu chạy bằng file .py thì tự đọc PostgreSQL fact_sales và chuẩn bị dữ liệu.

    Không còn load/xuất model_input_panel.csv và train_ready.csv nữa
    để thư mục input chỉ giữ 2 file chính:
    - train_sku.csv
    - test_sku.csv
    """
    global df_model_input, df_train_ready, project_root

    try:
        df_model_input
        df_train_ready
        return
    except NameError:
        pass

    root = _resolve_project_root()
    project_root = root
    df_model_input, df_train_ready = _prepare_forecast_input_from_fact_sales()


_auto_load_required_dataframes_if_needed()


# ============================================================
# 1. Cấu hình feature
# ============================================================

categorical_features = [
    "product_code",
    "group_code",
    "group_name",
    "line_name",
    "color",
]

numeric_features = [
    "fiscal_year",
    "fiscal_month",
    "qty",
    "revenue",
    "order_count",
    "active_dealer_count",
    "avg_unit_price",
    "qty_lag_1",
    "qty_lag_2",
    "revenue_lag_1",
    "revenue_lag_2",
    "rolling_2m_qty",
    "rolling_3m_qty",
    "mom_qty_growth",
    "mom_revenue_growth",
    "group_month_qty",
    "group_month_revenue",
    "sku_qty_share_in_group",
    "sku_revenue_share_in_group",
    "same_month_qty_last_year",
    "same_month_revenue_last_year",
    "sku_yoy_qty_growth",
    "sku_yoy_revenue_growth",
    "has_sku_yoy",
    "is_new_sku",
    "missing_master_flag",
    "missing_group_flag",
    "missing_line_flag",
    "missing_color_flag",
    "numeric_issue_flag",
    "outlier_flag",
]

target_col = "qty_next_month"


# ============================================================
# 2. Utility functions
# ============================================================

def rmse(y_true, y_pred) -> float:
    return mean_squared_error(y_true, y_pred) ** 0.5


def mape(y_true, y_pred) -> float:
    """MAPE bỏ qua các dòng actual = 0 để tránh chia cho 0."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mask = y_true != 0
    if mask.sum() == 0:
        return 0.0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def wmape(y_true, y_pred) -> float:
    """Weighted MAPE = tổng sai số tuyệt đối / tổng actual."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    denom = np.sum(np.abs(y_true))
    if denom == 0:
        return 0.0
    return float(np.sum(np.abs(y_true - y_pred)) / denom * 100)


def smape(y_true, y_pred) -> float:
    """sMAPE an toàn, không phát sinh warning chia cho 0."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    denom = (np.abs(y_true) + np.abs(y_pred)) / 2
    out = np.zeros_like(denom, dtype=float)
    np.divide(np.abs(y_true - y_pred), denom, out=out, where=denom != 0)
    return float(np.mean(out) * 100)


def bias_pct(y_true, y_pred) -> float:
    """Dấu dương = mô hình dự báo cao hơn thực tế, dấu âm = thấp hơn thực tế."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    denom = np.sum(y_true)
    if denom == 0:
        return 0.0
    return float((np.sum(y_pred) - np.sum(y_true)) / denom * 100)


def evaluate_forecast(
    df_eval: pd.DataFrame,
    actual_col: str,
    pred_col: str,
    model_name: str,
) -> dict:
    y_true = df_eval[actual_col].astype(float)
    y_pred = df_eval[pred_col].astype(float).clip(lower=0)

    return {
        "model": model_name,
        "MAE": mean_absolute_error(y_true, y_pred),
        "RMSE": rmse(y_true, y_pred),
        "MAPE": mape(y_true, y_pred),
        "WMAPE": wmape(y_true, y_pred),
        "sMAPE": smape(y_true, y_pred),
        "BiasPct": bias_pct(y_true, y_pred),
        "R2": r2_score(y_true, y_pred) if len(y_true) > 1 else np.nan,
        "Actual_Total_Qty": float(np.sum(y_true)),
        "Pred_Total_Qty": float(np.sum(y_pred)),
        "Top10_HitRate": top_k_hit_rate(df_eval.assign(**{pred_col: y_pred}), actual_col, pred_col, 10),
        "Top20_HitRate": top_k_hit_rate(df_eval.assign(**{pred_col: y_pred}), actual_col, pred_col, 20),
    }


def top_k_hit_rate(df_eval: pd.DataFrame, actual_col: str, pred_col: str, k: int = 20) -> float:
    actual_top = set(
        df_eval.sort_values(actual_col, ascending=False)
        .head(k)["product_code"]
        .astype(str)
    )
    pred_top = set(
        df_eval.sort_values(pred_col, ascending=False)
        .head(k)["product_code"]
        .astype(str)
    )
    return len(actual_top & pred_top) / k


def safe_prepare_model_data(df_train_ready: pd.DataFrame) -> pd.DataFrame:
    """Chuẩn hóa dữ liệu train để tránh lỗi missing/categorical."""
    required_cols = categorical_features + numeric_features + [target_col]
    missing_cols = [c for c in required_cols if c not in df_train_ready.columns]
    if missing_cols:
        raise ValueError(f"Thiếu cột trong df_train_ready: {missing_cols}")

    data = df_train_ready.copy()

    for col in categorical_features:
        data[col] = data[col].astype("string").fillna("Unknown")

    for col in numeric_features:
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0)

    data[target_col] = pd.to_numeric(data[target_col], errors="coerce").fillna(0)

    return data


def rebuild_dynamic_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Tính lại lag/rolling/share sau khi thêm forecast tháng mới."""
    panel = panel.copy()
    panel = panel.sort_values(["product_code", "fiscal_year", "fiscal_month"])

    # Lag theo SKU
    panel["qty_lag_1"] = panel.groupby("product_code")["qty"].shift(1)
    panel["qty_lag_2"] = panel.groupby("product_code")["qty"].shift(2)
    panel["revenue_lag_1"] = panel.groupby("product_code")["revenue"].shift(1)
    panel["revenue_lag_2"] = panel.groupby("product_code")["revenue"].shift(2)

    # Rolling theo SKU, không dùng tháng hiện tại để tránh leakage
    panel["rolling_2m_qty"] = (
        panel.groupby("product_code")["qty"]
        .shift(1)
        .rolling(window=2, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )

    panel["rolling_3m_qty"] = (
        panel.groupby("product_code")["qty"]
        .shift(1)
        .rolling(window=3, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )

    panel["mom_qty_growth"] = np.where(
        panel["qty_lag_1"] > 0,
        (panel["qty"] - panel["qty_lag_1"]) / panel["qty_lag_1"],
        0,
    )

    panel["mom_revenue_growth"] = np.where(
        panel["revenue_lag_1"] > 0,
        (panel["revenue"] - panel["revenue_lag_1"]) / panel["revenue_lag_1"],
        0,
    )

    # Tổng nhóm theo tháng
    panel = panel.drop(columns=["group_month_qty", "group_month_revenue"], errors="ignore")

    group_month_total = (
        panel.groupby(["fiscal_year", "fiscal_month", "group_name"], dropna=False)
        .agg(
            group_month_qty=("qty", "sum"),
            group_month_revenue=("revenue", "sum"),
        )
        .reset_index()
    )

    panel = panel.merge(
        group_month_total,
        on=["fiscal_year", "fiscal_month", "group_name"],
        how="left",
    )

    panel["sku_qty_share_in_group"] = np.where(
        panel["group_month_qty"] > 0,
        panel["qty"] / panel["group_month_qty"],
        0,
    )

    panel["sku_revenue_share_in_group"] = np.where(
        panel["group_month_revenue"] > 0,
        panel["revenue"] / panel["group_month_revenue"],
        0,
    )

    dynamic_cols = [
        "qty_lag_1", "qty_lag_2",
        "revenue_lag_1", "revenue_lag_2",
        "rolling_2m_qty", "rolling_3m_qty",
        "mom_qty_growth", "mom_revenue_growth",
        "group_month_qty", "group_month_revenue",
        "sku_qty_share_in_group", "sku_revenue_share_in_group",
    ]

    panel[dynamic_cols] = (
        panel[dynamic_cols]
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0)
    )

    return panel


def make_forecast_input_from_latest_state(
    latest_state: pd.DataFrame,
    current_year: int,
    current_month: int,
) -> pd.DataFrame:
    """
    Tạo input để dự báo tháng kế tiếp.

    Ví dụ:
    - Để forecast T4/2026, input phải là trạng thái T3/2026.
      current_year=2026, current_month=3.
    - Để forecast T5/2026, input là trạng thái forecast T4/2026.
      current_year=2026, current_month=4.
    """
    x = latest_state.copy()
    x["fiscal_year"] = current_year
    x["fiscal_month"] = current_month
    x["year_month"] = f"{current_year}-{current_month:02d}"

    for col in categorical_features:
        x[col] = x[col].astype("string").fillna("Unknown")

    for col in numeric_features:
        if col not in x.columns:
            x[col] = 0
        x[col] = pd.to_numeric(x[col], errors="coerce").fillna(0)

    return x


def predict_next_month(
    model,
    current_state: pd.DataFrame,
    target_year: int,
    target_month: int,
) -> pd.DataFrame:
    """
    Dự báo target_month bằng current_state.
    current_state phải là tháng liền trước target_month.
    """
    x = current_state.copy()

    for col in categorical_features:
        x[col] = x[col].astype("string").fillna("Unknown")

    for col in numeric_features:
        x[col] = pd.to_numeric(x[col], errors="coerce").fillna(0)

    pred_qty = model.predict(x[categorical_features + numeric_features])
    pred_qty = np.clip(pred_qty, 0, None)

    out = x.copy()
    out["forecast_year"] = target_year
    out["forecast_month"] = target_month
    out["forecast_qty_raw"] = pred_qty
    out["forecast_revenue_raw"] = out["forecast_qty_raw"] * out["avg_unit_price"]

    return out


def add_next_state_to_panel(
    panel: pd.DataFrame,
    forecast_result: pd.DataFrame,
) -> pd.DataFrame:
    """
    Đưa forecast của target_month thành một dòng state mới để dùng forecast tháng tiếp theo.
    """
    next_state = forecast_result.copy()

    next_state["fiscal_year"] = next_state["forecast_year"]
    next_state["fiscal_month"] = next_state["forecast_month"]
    next_state["year_month"] = (
        next_state["fiscal_year"].astype(str)
        + "-"
        + next_state["fiscal_month"].astype(str).str.zfill(2)
    )

    next_state["qty"] = next_state["forecast_qty_raw"]
    next_state["revenue"] = next_state["forecast_revenue_raw"]

    # Các biến chưa biết trong tương lai: đặt 0 hoặc giữ ước lượng đơn giản
    next_state["order_count"] = 0
    next_state["active_dealer_count"] = 0
    next_state["same_month_qty_last_year"] = 0
    next_state["same_month_revenue_last_year"] = 0
    next_state["sku_yoy_qty_growth"] = 0
    next_state["sku_yoy_revenue_growth"] = 0
    next_state["has_sku_yoy"] = 0
    next_state["numeric_issue_flag"] = 0
    next_state["qty_outlier_flag"] = 0
    next_state["revenue_outlier_flag"] = 0
    next_state["outlier_flag"] = 0

    # Chỉ giữ các cột có trong panel
    for col in panel.columns:
        if col not in next_state.columns:
            next_state[col] = 0

    next_state = next_state[panel.columns]

    updated_panel = pd.concat([panel, next_state], ignore_index=True)
    updated_panel = rebuild_dynamic_features(updated_panel)

    return updated_panel


def build_group_share_forecast_q2(df_model_input: pd.DataFrame) -> pd.DataFrame:
    """
    (DEPRECATED) Benchmark ổn định theo group-share của phiên bản trước.

    File này đã chuyển sang lựa chọn benchmark thuần dữ liệu (naive/rolling/EWMA)
    và so sánh trực tiếp với các mô hình tree-based.
    """
    raise NotImplementedError(
        "build_group_share_forecast_q2 is deprecated; use naive/rolling/EWMA benchmarks instead."
    )



# ============================================================
# 3. Data-driven benchmark selection + ML comparison
# ============================================================

model_data = safe_prepare_model_data(df_train_ready)

# Validation theo thời gian: dùng các dòng tháng 3 có target để mô phỏng dự báo tháng kế tiếp.
# Nếu dữ liệu mở rộng thêm nhiều năm, đoạn này vẫn chạy được.
test_mask = model_data["fiscal_month"] == 3
train_df = model_data[~test_mask].copy()
test_df = model_data[test_mask].copy()

# Xuất train/test dùng cho kiểm chứng và tái lập kết quả.
# Chỉ giữ 2 file chính để thư mục input gọn: train_sku.csv và test_sku.csv.
_input_dir_for_split = _resolve_project_root() / INPUT_SUBDIR
_input_dir_for_split.mkdir(parents=True, exist_ok=True)

# Xoá các file input cũ của phiên bản trước để tránh nhầm lẫn.
for _old_input_file in [
    "model_input_panel.csv",
    "train_ready.csv",
    "train.csv",
    "test.csv",
    "train_sku.csv",
    "test_sku.csv",
]:
    _old_path = _input_dir_for_split / _old_input_file
    if _old_path.exists():
        _old_path.unlink()

train_df.to_csv(_input_dir_for_split / "train_sku.csv", index=False, encoding=CSV_ENCODING)
test_df.to_csv(_input_dir_for_split / "test_sku.csv", index=False, encoding=CSV_ENCODING)

print(f"Saved selected train/test input CSVs to: {_input_dir_for_split}")
print(f"- train_sku.csv: {train_df.shape}")
print(f"- test_sku.csv: {test_df.shape}")

if train_df.empty or test_df.empty:
    raise ValueError(
        "Không đủ dữ liệu để backtest. Cần có cả train_df và test_df sau khi tách fiscal_month == 3."
    )


def safe_ratio(numerator, denominator, default=1.0):
    numerator = pd.to_numeric(numerator, errors="coerce")
    denominator = pd.to_numeric(denominator, errors="coerce")
    return np.where(denominator > 0, numerator / denominator, default)

# EWMA alpha grid: alpha càng cao thì càng tin tháng gần nhất nhiều hơn.
# Code sẽ backtest từng alpha và tự chọn alpha có WMAPE thấp nhất.
EWMA_ALPHA_GRID = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


def format_ewma_model_name(alpha: float) -> str:
    return f"benchmark_ewma_2m_a{int(round(alpha * 100)):02d}"


def parse_ewma_alpha(method_name: str) -> float:
    """Đọc alpha từ tên model, ví dụ benchmark_ewma_2m_a70 -> 0.70."""
    try:
        return int(method_name.rsplit("a", 1)[1]) / 100
    except Exception:
        # Fallback cho tên cũ nếu có
        return 0.7


def add_benchmark_predictions(
    df_eval: pd.DataFrame,
) -> pd.DataFrame:
    """Sinh nhiều benchmark ứng viên để backtest, hoàn toàn dựa trên dữ liệu."""
    out = df_eval.copy()

    out["benchmark_naive_pred"] = out["qty"].clip(lower=0)
    out["benchmark_rolling_2m_pred"] = out["rolling_2m_qty"].clip(lower=0)
    out["benchmark_rolling_3m_pred"] = out["rolling_3m_qty"].clip(lower=0)

    # EWMA 2 tháng: không cố định 0.7 nữa.
    # Thử nhiều alpha, sau đó chọn alpha thắng bằng WMAPE ở bước backtest.
    for alpha in EWMA_ALPHA_GRID:
        model_name = format_ewma_model_name(alpha)
        out[f"{model_name}_pred"] = (
            alpha * out["qty"] + (1 - alpha) * out["qty_lag_1"]
        ).clip(lower=0)

    return out


# -------------------------
# 3.1 Benchmark backtest
# -------------------------

test_eval = add_benchmark_predictions(
    test_df,
)

benchmark_pred_cols = {
    "benchmark_naive": "benchmark_naive_pred",
    "benchmark_rolling_2m": "benchmark_rolling_2m_pred",
    "benchmark_rolling_3m": "benchmark_rolling_3m_pred",
    **{format_ewma_model_name(alpha): f"{format_ewma_model_name(alpha)}_pred" for alpha in EWMA_ALPHA_GRID},
}

metrics_rows = []
for method_name, pred_col in benchmark_pred_cols.items():
    metrics_rows.append(evaluate_forecast(test_eval, target_col, pred_col, method_name))


# -------------------------
# 3.2 ML tree-based backtest
# -------------------------

def make_preprocess() -> ColumnTransformer:
    return ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_features),
            ("num", "passthrough", numeric_features),
        ],
        remainder="drop",
    )


model_specs = {
    "dt": DecisionTreeRegressor(
        max_depth=8,
        min_samples_leaf=5,
        random_state=42,
    ),
    "rf": RandomForestRegressor(
        n_estimators=500,
        max_depth=10,
        min_samples_leaf=3,
        random_state=42,
        n_jobs=-1,
    ),
    "extra_trees": ExtraTreesRegressor(
        n_estimators=500,
        max_depth=12,
        min_samples_leaf=3,
        random_state=42,
        n_jobs=-1,
    ),
    "gb": GradientBoostingRegressor(
        n_estimators=300,
        learning_rate=0.03,
        max_depth=3,
        min_samples_leaf=5,
        random_state=42,
    ),
}

trained_validation_pipelines = {}
ml_model_names = list(model_specs.keys())

for model_name, estimator in model_specs.items():
    pipeline = Pipeline(
        steps=[
            ("preprocess", make_preprocess()),
            ("model", estimator),
        ]
    )

    pipeline.fit(train_df[categorical_features + numeric_features], train_df[target_col])

    pred_col = f"{model_name}_pred"
    test_eval[pred_col] = pipeline.predict(test_df[categorical_features + numeric_features])
    test_eval[pred_col] = test_eval[pred_col].clip(lower=0)

    metrics_rows.append(evaluate_forecast(test_eval, target_col, pred_col, model_name))
    trained_validation_pipelines[model_name] = pipeline

metrics_all = pd.DataFrame(metrics_rows)
metrics_all["is_ml_model"] = metrics_all["model"].isin(ml_model_names)
metrics_all["is_benchmark"] = metrics_all["model"].isin(benchmark_pred_cols.keys())

# Ưu tiên WMAPE vì SKU bán nhiều quan trọng hơn SKU rất nhỏ.
metrics_all = (
    metrics_all
    .sort_values(["WMAPE", "MAE", "RMSE", "Top20_HitRate"], ascending=[True, True, True, False])
    .reset_index(drop=True)
)

print("Model comparison:")
print(metrics_all)

best_overall_row = metrics_all.iloc[0]
best_overall_name = str(best_overall_row["model"])

best_ml_row = metrics_all[metrics_all["is_ml_model"]].iloc[0]
best_ml_model_name = str(best_ml_row["model"])

best_benchmark_row = metrics_all[metrics_all["is_benchmark"]].iloc[0]
best_benchmark_name = str(best_benchmark_row["model"])

print("Best overall by WMAPE:", best_overall_name)
print("Best benchmark by WMAPE:", best_benchmark_name)
print("Best ML model by WMAPE:", best_ml_model_name)

ewma_alpha_metrics = metrics_all[metrics_all["model"].str.startswith("benchmark_ewma_2m_a")].copy()
if not ewma_alpha_metrics.empty:
    ewma_alpha_metrics["alpha"] = ewma_alpha_metrics["model"].apply(parse_ewma_alpha)
    ewma_alpha_metrics = ewma_alpha_metrics.sort_values("alpha").reset_index(drop=True)
    best_ewma_alpha_row = ewma_alpha_metrics.sort_values(["WMAPE", "MAE", "RMSE"]).iloc[0]
    print(
        "Best EWMA alpha:",
        float(best_ewma_alpha_row["alpha"]),
        f"WMAPE={float(best_ewma_alpha_row['WMAPE']):.2f}%"
    )
else:
    ewma_alpha_metrics = pd.DataFrame()

selected_forecast_model = best_overall_name
selected_is_ml = bool(best_overall_row["is_ml_model"])
selected_is_benchmark = bool(best_overall_row["is_benchmark"])

forecast_selection_reason = (
    f"selected={selected_forecast_model}, WMAPE={float(best_overall_row['WMAPE']):.2f}%; "
    f"best_benchmark={best_benchmark_name}, WMAPE={float(best_benchmark_row['WMAPE']):.2f}%; "
    f"best_ml={best_ml_model_name}, WMAPE={float(best_ml_row['WMAPE']):.2f}%"
)

if selected_is_ml:
    print(f"FINAL FORECAST METHOD: ML - {selected_forecast_model}")
else:
    print(f"FINAL FORECAST METHOD: DATA-DRIVEN BENCHMARK - {selected_forecast_model}")
print(forecast_selection_reason)


# ============================================================
# 4. Forecast Q2/2026 bằng method thắng trong backtest
# ============================================================

def predict_next_month_by_benchmark(
    current_state: pd.DataFrame,
    method_name: str,
    target_year: int,
    target_month: int,
) -> pd.DataFrame:
    """Dự báo tháng kế tiếp bằng benchmark đã thắng validation."""
    x = current_state.copy()

    if method_name == "benchmark_naive":
        pred_qty = x["qty"]

    elif method_name == "benchmark_rolling_2m":
        pred_qty = x["rolling_2m_qty"]

    elif method_name == "benchmark_rolling_3m":
        pred_qty = x["rolling_3m_qty"]

    elif method_name.startswith("benchmark_ewma_2m_a") or method_name == "benchmark_ewma_2m":
        alpha = parse_ewma_alpha(method_name)
        pred_qty = alpha * x["qty"] + (1 - alpha) * x["qty_lag_1"]

    else:
        raise ValueError(f"Benchmark method không hợp lệ: {method_name}")

    out = x.copy()
    out["forecast_year"] = target_year
    out["forecast_month"] = target_month
    out["forecast_qty_raw"] = pd.Series(pred_qty, index=out.index).fillna(0).clip(lower=0)
    out["forecast_revenue_raw"] = out["forecast_qty_raw"] * out["avg_unit_price"]
    return out


forecast_panel = df_model_input.copy()
forecast_panel = forecast_panel.sort_values(["product_code", "fiscal_year", "fiscal_month"])
forecast_panel = rebuild_dynamic_features(forecast_panel)

current_state = forecast_panel[
    (forecast_panel["fiscal_year"] == 2026)
    & (forecast_panel["fiscal_month"] == 3)
].copy()

if current_state.empty:
    raise ValueError("Không tìm thấy state T3/2026 trong df_model_input để forecast Q2/2026.")

future_targets = [(2026, 4), (2026, 5), (2026, 6)]
forecast_rows = []

if selected_is_ml:
    final_ml_model = Pipeline(
        steps=[
            ("preprocess", make_preprocess()),
            ("model", model_specs[selected_forecast_model]),
        ]
    )
    final_ml_model.fit(
        model_data[categorical_features + numeric_features],
        model_data[target_col],
    )
    print("Final selected ML model trained:", selected_forecast_model)

    for target_year, target_month in future_targets:
        pred = predict_next_month(
            model=final_ml_model,
            current_state=current_state,
            target_year=target_year,
            target_month=target_month,
        )
        forecast_rows.append(pred)
        forecast_panel = add_next_state_to_panel(forecast_panel, pred)
        current_state = forecast_panel[
            (forecast_panel["fiscal_year"] == target_year)
            & (forecast_panel["fiscal_month"] == target_month)
        ].copy()

else:
    print("Không dùng ML làm forecast chính. Dùng benchmark đã thắng backtest:", selected_forecast_model)

    for target_year, target_month in future_targets:
        pred = predict_next_month_by_benchmark(
            current_state=current_state,
            method_name=selected_forecast_model,
            target_year=target_year,
            target_month=target_month,
        )
        forecast_rows.append(pred)
        forecast_panel = add_next_state_to_panel(forecast_panel, pred)
        current_state = forecast_panel[
            (forecast_panel["fiscal_year"] == target_year)
            & (forecast_panel["fiscal_month"] == target_month)
        ].copy()

forecast_main = pd.concat(forecast_rows, ignore_index=True)

# LƯU Ý QUAN TRỌNG:
# forecast_rows vẫn giữ fiscal_year/fiscal_month của current_state
# (tức tháng dùng làm input, ví dụ T3 để dự báo T4).
# Đồng thời nó cũng có forecast_year/forecast_month là tháng cần dự báo.
# Nếu rename forecast_year -> fiscal_year ngay thì DataFrame sẽ có 2 cột fiscal_year,
# gây lỗi: ValueError: Grouper for 'fiscal_year' not 1-dimensional.
# Vì vậy phải bỏ fiscal_year/fiscal_month cũ trước, rồi mới rename tháng forecast.
forecast_main = forecast_main.drop(
    columns=["fiscal_year", "fiscal_month", "year_month"],
    errors="ignore",
)
forecast_main = forecast_main.rename(columns={
    "forecast_year": "fiscal_year",
    "forecast_month": "fiscal_month",
})

# Chặn lỗi cột trùng tên sau các bước merge/rename khác.
forecast_main = forecast_main.loc[:, ~forecast_main.columns.duplicated()].copy()
forecast_main["fiscal_year"] = pd.to_numeric(forecast_main["fiscal_year"], errors="coerce").astype("Int64")
forecast_main["fiscal_month"] = pd.to_numeric(forecast_main["fiscal_month"], errors="coerce").astype("Int64")

forecast_main["selected_forecast_model"] = selected_forecast_model
forecast_main["forecast_selection_type"] = np.where(selected_is_ml, "ml_tree_based", "data_driven_benchmark")

print("Forecast main shape:", forecast_main.shape)
print(
    forecast_main[
        ["fiscal_year", "fiscal_month", "product_code", "product_name", "group_name",
         "forecast_qty_raw", "forecast_revenue_raw", "selected_forecast_model"]
    ].head()
)


# ============================================================
# 5. Calibration factor theo nhóm dựa trên backtest của method được chọn
# ============================================================

if selected_is_ml:
    selected_pred_col = f"{selected_forecast_model}_pred"
else:
    selected_pred_col = benchmark_pred_cols[selected_forecast_model]

group_calibration = (
    test_eval
    .groupby("group_name", dropna=False)
    .agg(
        actual_qty=(target_col, "sum"),
        pred_qty=(selected_pred_col, "sum"),
    )
    .reset_index()
)

group_calibration["raw_calibration_factor"] = np.where(
    group_calibration["pred_qty"] > 0,
    group_calibration["actual_qty"] / group_calibration["pred_qty"],
    1.0,
)

# Shrinkage calibration: chỉ chỉnh 50% độ lệch để tránh over-correct.
alpha = 0.5
group_calibration["calibration_factor"] = (
    1 + alpha * (group_calibration["raw_calibration_factor"] - 1)
)

group_calibration["calibration_factor"] = (
    group_calibration["calibration_factor"]
    .replace([np.inf, -np.inf], np.nan)
    .fillna(1.0)
    .clip(0.9, 1.1)
)

forecast_main = forecast_main.merge(
    group_calibration[["group_name", "calibration_factor"]],
    on="group_name",
    how="left",
)
forecast_main["calibration_factor"] = forecast_main["calibration_factor"].fillna(1.0)

for col in ["is_new_sku", "missing_master_flag"]:
    if col not in forecast_main.columns:
        forecast_main[col] = 0

forecast_main["forecast_qty_raw"] = forecast_main["forecast_qty_raw"].clip(lower=0)
forecast_main["forecast_revenue_raw"] = forecast_main["forecast_revenue_raw"].clip(lower=0)

forecast_main["forecast_qty_base"] = (
    forecast_main["forecast_qty_raw"] * forecast_main["calibration_factor"]
)
forecast_main["forecast_revenue_base"] = (
    forecast_main["forecast_qty_base"] * forecast_main["avg_unit_price"]
)


# ============================================================
# 6. Scenario dựa trên validation error của method được chọn
# ============================================================

selected_smape = float(best_overall_row["sMAPE"]) / 100
scenario_margin = min(max(selected_smape, 0.05), 0.20)

forecast_main["forecast_qty_conservative"] = forecast_main["forecast_qty_base"] * (1 - scenario_margin)
forecast_main["forecast_qty_optimistic"] = forecast_main["forecast_qty_base"] * (1 + scenario_margin)

forecast_main["forecast_revenue_conservative"] = forecast_main["forecast_qty_conservative"] * forecast_main["avg_unit_price"]
forecast_main["forecast_revenue_optimistic"] = forecast_main["forecast_qty_optimistic"] * forecast_main["avg_unit_price"]


# ============================================================
# 7. Output tables
# ============================================================

forecast_monthly_q2 = (
    forecast_main
    .groupby(["fiscal_year", "fiscal_month"], dropna=False)
    .agg(
        forecast_qty_conservative=("forecast_qty_conservative", "sum"),
        forecast_qty_base=("forecast_qty_base", "sum"),
        forecast_qty_optimistic=("forecast_qty_optimistic", "sum"),
        forecast_revenue_conservative=("forecast_revenue_conservative", "sum"),
        forecast_revenue_base=("forecast_revenue_base", "sum"),
        forecast_revenue_optimistic=("forecast_revenue_optimistic", "sum"),
    )
    .reset_index()
    .sort_values(["fiscal_year", "fiscal_month"])
)

forecast_group_q2 = (
    forecast_main
    .groupby(["group_code", "group_name"], dropna=False)
    .agg(
        forecast_qty_q2=("forecast_qty_base", "sum"),
        forecast_revenue_q2=("forecast_revenue_base", "sum"),
        forecast_qty_conservative=("forecast_qty_conservative", "sum"),
        forecast_qty_optimistic=("forecast_qty_optimistic", "sum"),
        forecast_revenue_conservative=("forecast_revenue_conservative", "sum"),
        forecast_revenue_optimistic=("forecast_revenue_optimistic", "sum"),
        sku_count=("product_code", "nunique"),
    )
    .reset_index()
    .sort_values("forecast_revenue_q2", ascending=False)
)

total_revenue = forecast_group_q2["forecast_revenue_q2"].sum()
forecast_group_q2["revenue_share_q2"] = np.where(
    total_revenue > 0,
    forecast_group_q2["forecast_revenue_q2"] / total_revenue,
    0,
)

forecast_sku_q2 = (
    forecast_main
    .groupby(
        ["product_code", "product_name", "group_code", "group_name", "line_name", "color"],
        dropna=False,
    )
    .agg(
        forecast_qty_q2=("forecast_qty_base", "sum"),
        forecast_revenue_q2=("forecast_revenue_base", "sum"),
        forecast_qty_conservative=("forecast_qty_conservative", "sum"),
        forecast_qty_optimistic=("forecast_qty_optimistic", "sum"),
        avg_unit_price=("avg_unit_price", "mean"),
        is_new_sku=("is_new_sku", "max"),
        missing_master_flag=("missing_master_flag", "max"),
    )
    .reset_index()
)

forecast_sku_q2["rank_qty"] = (
    forecast_sku_q2["forecast_qty_q2"]
    .rank(method="dense", ascending=False)
    .astype(int)
)

forecast_sku_q2["rank_revenue"] = (
    forecast_sku_q2["forecast_revenue_q2"]
    .rank(method="dense", ascending=False)
    .astype(int)
)


def build_reason(row: pd.Series) -> str:
    reasons = []

    if row["forecast_qty_q2"] >= forecast_sku_q2["forecast_qty_q2"].quantile(0.75):
        reasons.append("sản lượng dự báo thuộc nhóm cao")

    if row["forecast_revenue_q2"] >= forecast_sku_q2["forecast_revenue_q2"].quantile(0.75):
        reasons.append("đóng góp doanh thu tốt")

    if int(row.get("is_new_sku", 0)) == 1:
        reasons.append("SKU mới có tín hiệu bán trong năm 2026")

    if int(row.get("missing_master_flag", 0)) == 1:
        reasons.append("cần rà soát thêm master data")

    if not reasons:
        reasons.append("có tín hiệu nhu cầu ổn định theo phương pháp được backtest")

    return ", ".join(reasons).capitalize() + "."


top20_sku_q2 = (
    forecast_sku_q2
    .sort_values(["forecast_qty_q2", "forecast_revenue_q2"], ascending=False)
    .head(20)
    .copy()
)

top20_sku_q2["reason"] = top20_sku_q2.apply(build_reason, axis=1)


# ============================================================
# 8. Display & export
# ============================================================

selected_ewma_alpha = parse_ewma_alpha(selected_forecast_model) if selected_forecast_model.startswith("benchmark_ewma_2m_a") else np.nan

forecast_selection_summary = pd.DataFrame([
    {
        "selected_forecast_model": selected_forecast_model,
        "selected_ewma_alpha": selected_ewma_alpha,
        "forecast_selection_type": "ml_tree_based" if selected_is_ml else "data_driven_benchmark",
        "selected_wmape": float(best_overall_row["WMAPE"]),
        "best_ml_model": best_ml_model_name,
        "best_ml_wmape": float(best_ml_row["WMAPE"]),
        "best_benchmark_model": best_benchmark_name,
        "best_benchmark_wmape": float(best_benchmark_row["WMAPE"]),
        "selection_reason": forecast_selection_reason,
        "scenario_margin": scenario_margin,
    }
])

def resolve_output_dir() -> Path:
    """Trả về thư mục output chuẩn cho kết quả forecast Q2/2026."""
    try:
        root = Path(project_root)
    except NameError:
        root = Path.cwd()
        if not (root / "data").exists() and (root.parent / "data").exists():
            root = root.parent

    output_dir = root / OUTPUT_SUBDIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def save_csv(df: pd.DataFrame, file_name: str, output_dir: Path) -> None:
    """Lưu CSV thống nhất encoding UTF-8 BOM để mở tốt trên Excel/Power BI."""
    df.to_csv(output_dir / file_name, index=False, encoding=CSV_ENCODING)


# Chỉ xuất các file thật sự cần dùng cho báo cáo, dashboard và kiểm chứng model.
output_dir = resolve_output_dir()

sku_monthly_cols = [
    "fiscal_year",
    "fiscal_month",
    "product_code",
    "product_name",
    "group_code",
    "group_name",
    "line_name",
    "color",
    "avg_unit_price",
    "forecast_qty_base",
    "forecast_revenue_base",
    "forecast_qty_conservative",
    "forecast_qty_optimistic",
    "forecast_revenue_conservative",
    "forecast_revenue_optimistic",
    "calibration_factor",
    "selected_forecast_model",
    "forecast_selection_type",
]
sku_monthly_cols = [col for col in sku_monthly_cols if col in forecast_main.columns]
sku_monthly_q2 = (
    forecast_main[sku_monthly_cols]
    .sort_values(["fiscal_year", "fiscal_month", "forecast_revenue_base"], ascending=[True, True, False])
    .reset_index(drop=True)
)

export_tables = {
    "metrics.csv": metrics_all,
    "selection.csv": forecast_selection_summary,
    "calibration.csv": group_calibration,
    "monthly.csv": forecast_monthly_q2,
    "group.csv": forecast_group_q2,
    "sku_q2.csv": forecast_sku_q2,
    "sku_monthly.csv": sku_monthly_q2,
    "top20_sku.csv": top20_sku_q2,
}

if not ewma_alpha_metrics.empty:
    export_tables["ewma_alpha.csv"] = ewma_alpha_metrics

for file_name, df_out in export_tables.items():
    save_csv(df_out, file_name, output_dir)

print(f"\nSaved forecast CSV outputs to: {output_dir}")
print("Exported files:")
for file_name in export_tables:
    print(f"- {file_name}")
