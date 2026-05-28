"""LLM-based BI interpretation for TNBIKE."""

from __future__ import annotations

import json
import os
from typing import Any

from ai.common import load_environment, log_pending_issue, setup_logging
from ai.llm_client import GroqKeyPoolClient, parse_json_text


load_environment()
logger = setup_logging(__name__)


BI_SYSTEM_PROMPT = """
Bạn là chuyên gia phân tích kinh doanh cho Công ty Xe đạp Thống Nhất (TNBIKE) —
nhà sản xuất và phân phối xe đạp B2B với hơn 200 SKU, 700+ đại lý toàn quốc.

NHIỆM VỤ: Phân tích dữ liệu kinh doanh được cung cấp và tạo ra insights mới,
SÂU HƠN và MỞ RỘNG những gì đội ngũ dữ liệu đã phát hiện.

BASELINE INSIGHTS (đội ngũ dữ liệu đã xác nhận — dùng làm nền tảng, KHÔNG lặp lại):
{baseline_insights}

DỮ LIỆU THỰC TẾ HIỆN TẠI:
{data_context}

YÊU CẦU OUTPUT (JSON):
{{
  "extended_insights": [
    {{
      "title": "...",
      "finding": "Phát hiện cụ thể từ dữ liệu (có số liệu)",
      "business_impact": "Ý nghĩa kinh doanh và mức độ rủi ro/cơ hội",
      "action": "Khuyến nghị hành động cụ thể, có thể thực thi ngay",
      "confidence": "high/medium/low",
      "extends_baseline": "insight nào từ baseline mà phát hiện này mở rộng"
    }}
  ],
  "hidden_patterns": ["pattern 1", "pattern 2"],
  "risk_alerts": ["cảnh báo rủi ro cụ thể nếu có"],
  "quick_wins": ["hành động có thể làm ngay trong 30 ngày"]
}}

Trả về JSON thuần túy, không markdown, không giải thích thêm.
"""


def _strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def parse_llm_json(text: str) -> dict[str, Any]:
    return parse_json_text(text)


def _fallback(reason: str, raw_text: str = "") -> dict[str, Any]:
    return {
        "status": "LLM_UNAVAILABLE",
        "reason": reason,
        "raw_text": raw_text,
        "extended_insights": [],
        "hidden_patterns": [],
        "risk_alerts": [reason],
        "quick_wins": [],
    }


def interpret_bi_context(
    baseline_insights: str,
    data_context: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        return {
            "status": "DRY_RUN",
            "extended_insights": [],
            "hidden_patterns": [],
            "risk_alerts": [],
            "quick_wins": [],
            "context_chars": len(data_context),
        }

    prompt = BI_SYSTEM_PROMPT.format(
        baseline_insights=baseline_insights,
        data_context=data_context,
    )
    client = GroqKeyPoolClient()
    if not client.has_keys():
        reason = f"{client.provider.upper()} API keys are missing; skipped BI LLM interpretation."
        logger.warning(reason)
        log_pending_issue(reason)
        return _fallback(reason)

    try:
        parsed = client.chat_json(prompt=prompt)
        parsed.setdefault("status", "SUCCESS")
        parsed.setdefault("llm_provider", client.provider)
        parsed.setdefault("llm_model", client.model)
        return parsed
    except json.JSONDecodeError as exc:
        last_error = f"Không thể đọc JSON từ {client.provider}: {exc}"
        logger.warning(last_error)
        return _fallback(last_error)
    except Exception as exc:
        last_error = f"Diễn giải BI bằng {client.provider} không thành công sau khi đổi khóa: {exc}"
        logger.warning(last_error)
        log_pending_issue(last_error)
        return _fallback(last_error)
