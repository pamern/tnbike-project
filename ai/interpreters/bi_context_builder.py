"""Build compact LLM context from BI extraction output."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd


BASELINE_INSIGHTS = [
    "INSIGHT 1 - Loyalty Gap: Q1/2026 revenue growth has not translated into loyalty; 41% of dealers bought once, 274 dealers stopped >380 days, VIP+Active dealers create >70% revenue.",
    "INSIGHT 2 - Premium Decline: Growth comes from mass segments while SPORTBIKE_A and SPORTBIKE_S declined despite higher ASP.",
    "INSIGHT 3 - SKU Bloat: Stars and Dogs each have 27 lines; color revenue is concentrated in black/cream/gray while several colors are weak.",
    "INSIGHT 4 - Natural Bundle: CITYBIKE_P and KIDBIKE_1 co-occur in about 45% of orders while AOV and products/order are declining.",
    "INSIGHT 5 - Geographic Shift: The North has about 80% dealers but slower growth; Central provinces are growing strongly; each region depends on one lead province.",
    "INSIGHT 6 - Highland Plateau: Northern midland/mountain region contributes 9% revenue but only 4% YoY growth; expansion ROI is lower than Central region.",
]


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _frame_records(df: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    safe = df.head(limit).copy()
    return json.loads(safe.to_json(orient="records", force_ascii=False, date_format="iso"))


def build_baseline_context() -> str:
    return "\n".join(f"- {item}" for item in BASELINE_INSIGHTS)


def build_bi_context(
    extracted: dict[str, pd.DataFrame],
    max_chars: int = 12000,
) -> str:
    payload = {
        "operational_kpis": _frame_records(extracted.get("operational_kpis"), 5),
        "revenue_trend": _frame_records(extracted.get("revenue_trend"), 24),
        "product_analysis": _frame_records(extracted.get("product_analysis"), 20),
        "customer_rfm_top": _frame_records(extracted.get("customer_rfm"), 30),
        "geo_analysis_top": _frame_records(extracted.get("geo_analysis"), 30),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)

    if len(text) <= max_chars:
        return text

    compact_payload = {
        "operational_kpis": payload["operational_kpis"],
        "revenue_trend": payload["revenue_trend"][-12:],
        "product_analysis": payload["product_analysis"][:10],
        "customer_rfm_top": payload["customer_rfm_top"][:15],
        "geo_analysis_top": payload["geo_analysis_top"][:15],
        "context_note": "Context was compacted to stay within token budget.",
    }
    text = json.dumps(compact_payload, ensure_ascii=False, indent=2, default=_json_default)
    return text[:max_chars]


def build_context_package(extracted: dict[str, pd.DataFrame]) -> dict[str, str]:
    return {
        "baseline_insights": build_baseline_context(),
        "data_context": build_bi_context(extracted),
    }

