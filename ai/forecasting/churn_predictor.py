"""Dealer churn prediction for TNBIKE."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ai.common import log_pending_issue, setup_logging


logger = setup_logging(__name__)


FEATURE_COLS = [
    "days_since_last_order",
    "order_frequency",
    "total_revenue",
    "avg_order_value",
    "recent_90d_revenue",
    "recent_90d_orders",
    "revenue_trend",
    "rfm_score",
]


def _heuristic_churn_probability(df: pd.DataFrame) -> pd.Series:
    recency_component = np.clip(df["days_since_last_order"] / 180, 0, 1) * 0.55
    frequency_component = (1 - np.clip(df["order_frequency"] / 6, 0, 1)) * 0.20
    trend_component = np.where(df["revenue_trend"] < 0, np.clip(abs(df["revenue_trend"]), 0, 1) * 0.20, 0)
    rfm_component = (1 - np.clip(df["rfm_score"] / 15, 0, 1)) * 0.05
    return pd.Series(np.clip(recency_component + frequency_component + trend_component + rfm_component, 0, 1), index=df.index)


def predict_churn(customer_features: pd.DataFrame) -> dict[str, Any]:
    if customer_features.empty:
        reason = "No customer features available; churn prediction skipped."
        log_pending_issue(reason)
        return {"status": "NO_DATA", "customers": [], "summary": {"warning": reason}}

    df = customer_features.copy()
    for col in FEATURE_COLS:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0)

    df["target_no_order_30d_proxy"] = (df["days_since_last_order"] > 30).astype(int)
    method = "heuristic"

    try:
        y = df["target_no_order_30d_proxy"]
        if y.nunique() >= 2 and len(df) >= 50:
            x_train, x_test, y_train, _ = train_test_split(
                df[FEATURE_COLS],
                y,
                test_size=0.25,
                random_state=42,
                stratify=y,
            )
            model: Pipeline | RandomForestClassifier
            if len(df) < 200:
                model = Pipeline(
                    steps=[
                        ("scale", StandardScaler()),
                        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
                    ]
                )
            else:
                model = RandomForestClassifier(
                    n_estimators=300,
                    min_samples_leaf=5,
                    random_state=42,
                    class_weight="balanced",
                )
            model.fit(x_train, y_train)
            df["churn_probability"] = model.predict_proba(df[FEATURE_COLS])[:, 1]
            method = type(model).__name__
        else:
            df["churn_probability"] = _heuristic_churn_probability(df)
    except Exception as exc:
        reason = f"Mô hình rời bỏ không thành công; chuyển sang phương án heuristic dự phòng. Lỗi: {exc}"
        logger.warning(reason)
        log_pending_issue(reason)
        df["churn_probability"] = _heuristic_churn_probability(df)

    df["priority_score"] = (
        df["churn_probability"]
        * np.log1p(df["total_revenue"].clip(lower=0))
        * np.clip(df["rfm_score"], 1, 15)
    ).round(4)
    output_cols = [
        "customer_code",
        "customer_name",
        "province_name",
        "region",
        "days_since_last_order",
        "order_frequency",
        "total_revenue",
        "revenue_trend",
        "rfm_score",
        "churn_probability",
        "priority_score",
    ]
    result = df.sort_values("priority_score", ascending=False)[output_cols].reset_index(drop=True)
    high_risk = result[result["churn_probability"] >= 0.6]
    return {
        "status": "SUCCESS",
        "method": method,
        "customers": result.to_dict(orient="records"),
        "high_risk_customers": high_risk.head(50).to_dict(orient="records"),
        "summary": {
            "customer_count": int(len(result)),
            "high_risk_count": int(len(high_risk)),
            "top_priority_dealers": result.head(5)["customer_code"].tolist(),
        },
    }
