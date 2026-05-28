"""Build compact LLM context from BI extraction output."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd


BASELINE_INSIGHTS = [
    "INSIGHT 1 - Khoảng trống trung thành: tăng trưởng doanh thu Q1/2026 chưa chuyển hóa thành loyalty; 41% đại lý chỉ mua một lần, 274 đại lý ngừng giao dịch trên 380 ngày, nhóm VIP+Active tạo trên 70% doanh thu.",
    "INSIGHT 2 - Suy giảm phân khúc cao cấp: tăng trưởng đến từ nhóm phổ thông, trong khi SPORTBIKE_A và SPORTBIKE_S giảm dù giá bán bình quân cao hơn.",
    "INSIGHT 3 - Danh mục SKU phình to: nhóm Stars và Dogs mỗi nhóm có 27 dòng; doanh thu màu tập trung ở đen, kem, ghi trong khi nhiều màu rất yếu.",
    "INSIGHT 4 - Bundle tự nhiên: CITYBIKE_P và KIDBIKE_1 xuất hiện cùng nhau trong khoảng 45% đơn hàng, trong khi AOV và số sản phẩm trên đơn giảm.",
    "INSIGHT 5 - Dịch chuyển địa lý: miền Bắc có khoảng 80% đại lý nhưng tăng trưởng chậm lại; miền Trung tăng mạnh; mỗi miền phụ thuộc vào một tỉnh đầu tàu.",
    "INSIGHT 6 - Cao nguyên tăng trưởng: Trung du miền núi phía Bắc đóng góp 9% doanh thu nhưng chỉ tăng 4% YoY; ROI mở rộng thấp hơn miền Trung.",
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
        "context_note": "Ngữ cảnh đã được rút gọn để giữ trong giới hạn token.",
    }
    text = json.dumps(compact_payload, ensure_ascii=False, indent=2, default=_json_default)
    return text[:max_chars]


def build_context_package(extracted: dict[str, pd.DataFrame]) -> dict[str, str]:
    return {
        "baseline_insights": build_baseline_context(),
        "data_context": build_bi_context(extracted),
    }
