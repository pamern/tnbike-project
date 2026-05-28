"""LLM reasoning over forecast outputs."""

from __future__ import annotations

import json
import os
from typing import Any

from ai.common import load_environment, log_pending_issue, setup_logging
from ai.llm_client import GroqKeyPoolClient, parse_json_text


load_environment()
logger = setup_logging(__name__)


FORECAST_SYSTEM_PROMPT = """
Bạn là chuyên gia chiến lược cho TNBIKE. Dựa trên kết quả mô hình dự báo,
hãy diễn giải và đề xuất chiến lược kinh doanh cho Q2/2026.

KẾT QUẢ DỰ BÁO:
{forecast_results}

DANH SÁCH ĐẠI LÝ RỦI RO CAO:
{churn_list}

YÊU CẦU OUTPUT (JSON):
{{
  "q2_forecast_summary": {{
    "total_revenue_forecast": "...",
    "growth_vs_q1": "...",
    "top_products": ["..."],
    "risk_products": ["..."]
  }},
  "color_strategy": {{
    "rising_colors": ["..."],
    "declining_colors": ["..."],
    "recommendation": "..."
  }},
  "dealer_actions": {{
    "high_risk_count": 0,
    "retention_priority": ["top 5 dealer codes"],
    "reactivation_targets": ["..."],
    "strategy": "..."
  }},
  "strategic_recommendations": [
    {{
      "area": "product/geo/dealer/pricing",
      "action": "...",
      "expected_impact": "...",
      "timeline": "Q2/tháng 4/tháng 5/tháng 6"
    }}
  ]
}}

Trả về JSON thuần túy, không markdown.
"""


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _compact_payload(payload: Any, max_chars: int = 12000) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)
    return text[:max_chars]


def _strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def _fallback(forecast_results: dict[str, Any], churn_results: dict[str, Any], reason: str) -> dict[str, Any]:
    demand_summary = forecast_results.get("demand", {}).get("summary", {})
    color_results = forecast_results.get("colors", {})
    return {
        "status": "LLM_UNAVAILABLE",
        "reason": reason,
        "q2_forecast_summary": {
            "total_revenue_forecast": demand_summary.get("q2_total_revenue_forecast", 0),
            "growth_vs_q1": "LLM_UNAVAILABLE",
            "top_products": demand_summary.get("top_groups", []),
            "risk_products": [],
        },
        "color_strategy": {
            "rising_colors": [r.get("color") for r in color_results.get("rising_colors", [])[:5]],
            "declining_colors": [r.get("color") for r in color_results.get("declining_colors", [])[:5]],
            "recommendation": "Review rising/declining color lists from model output.",
        },
        "dealer_actions": {
            "high_risk_count": churn_results.get("summary", {}).get("high_risk_count", 0),
            "retention_priority": churn_results.get("summary", {}).get("top_priority_dealers", []),
            "reactivation_targets": [],
            "strategy": "Prioritize dealers with high churn probability and high revenue contribution.",
        },
        "strategic_recommendations": [],
    }


def reason_over_forecasts(
    forecast_results: dict[str, Any],
    churn_results: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        return _fallback(forecast_results, churn_results, "DRY_RUN")

    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider != "groq":
        reason = f"Unsupported LLM_PROVIDER={provider}; only groq is configured for this project."
        log_pending_issue(reason)
        return _fallback(forecast_results, churn_results, reason)

    client = GroqKeyPoolClient()
    if not client.has_keys():
        reason = "GROQ_API_KEYS is missing; skipped forecast LLM reasoning."
        log_pending_issue(reason)
        return _fallback(forecast_results, churn_results, reason)

    prompt = FORECAST_SYSTEM_PROMPT.format(
        forecast_results=_compact_payload(forecast_results),
        churn_list=_compact_payload(churn_results.get("high_risk_customers", [])[:25]),
    )

    try:
        parsed = client.chat_json(prompt=prompt)
        parsed.setdefault("status", "SUCCESS")
        parsed.setdefault("llm_provider", "groq")
        parsed.setdefault("llm_model", client.model)
        return parsed
    except Exception as exc:
        last_error = f"Groq forecast reasoning failed after key rotation: {exc}"
        logger.warning(last_error)
        log_pending_issue(last_error)
        return _fallback(forecast_results, churn_results, last_error)
