"""Strategic intelligence synthesis for executive-grade reports."""

from __future__ import annotations

import json
from typing import Any

from ai.common import log_pending_issue, setup_logging
from ai.llm_client import GroqKeyPoolClient


logger = setup_logging(__name__)


STRATEGY_SYSTEM_PROMPT = """
Bạn là senior strategy consultant cho TNBIKE, không phải BI summarizer.
Nhiệm vụ của bạn là biến tín hiệu dữ liệu thành Executive Intelligence Report có discovery value cao.

Quy tắc bắt buộc:
- Không viết insight theo template nông "Finding/Impact/Action" nếu không có causal reasoning.
- Mỗi insight phải trả lời: điều gì xảy ra, vì sao, tác động dài hạn, hậu quả nếu không xử lý, đòn bẩy tối ưu, action ROI cao nhất.
- Luôn so sánh đa chiều giữa geography, dealer cohort, product lifecycle, seasonality, margin/price structure, churn risk, inventory velocity proxy và data quality.
- Phải gọi rõ quantitative evidence, benchmark comparison, root-cause reasoning, strategic implication.
- Phát hiện contradiction: ví dụ tăng trưởng revenue nhưng loyalty/churn xấu; growth category nhưng margin/ASP yếu; forecast tăng nhưng data quality Unknown cao.
- Meta-analysis là bắt buộc: tự đánh giá độ tin cậy dữ liệu và coi vùng Unknown/missing mapping là business risk nếu material.
- Recommendation phải có prioritization_score, expected_business_impact, implementation_complexity, estimated_roi, execution_dependency, timeline.
- Insight kém là insight hiển nhiên, chỉ nhắc lại aggregation, hoặc không actionable. Loại bỏ chúng trong self-critique.
- Cấm dùng nguyên nhân vòng lặp như "tăng vì nhu cầu tăng", "do khách hàng thích", "quảng cáo nhiều hơn" nếu không có cơ chế vận hành hoặc dữ liệu hỗ trợ.
- Cấm recommendation chung chung như "tăng quảng cáo/khuyến mãi" nếu không có cohort, SKU, geography, dependency và ROI logic.
- Nếu evidence không đủ để khẳng định nguyên nhân, phải nói là "hypothesis" và nêu cách kiểm chứng.
- Chỉ trả JSON hợp lệ.
"""


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _top(rows: list[dict[str, Any]], key: str, n: int = 5, reverse: bool = True) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: _num(row.get(key)), reverse=reverse)[:n]


def extract_statistical_signals(
    bi_result: dict[str, Any],
    forecast_result: dict[str, Any],
) -> dict[str, Any]:
    data = bi_result.get("data", {})
    product_rows = data.get("product_analysis", [])
    geo_rows = data.get("geo_analysis", [])
    trend_rows = data.get("revenue_trend", [])
    kpi = (data.get("operational_kpis") or [{}])[0]
    customer_rows = data.get("customer_rfm", [])

    total_revenue = _num(kpi.get("total_revenue"))
    unknown_product = next(
        (row for row in product_rows if str(row.get("group_name", "")).lower() == "unknown" or str(row.get("group_code", "")).lower() == "unknown"),
        {},
    )
    unknown_share = _num(unknown_product.get("total_revenue")) / total_revenue if total_revenue else 0

    trend_sorted = sorted(trend_rows, key=lambda r: (r.get("fiscal_year", 0), r.get("fiscal_month", 0)))
    month_growth = []
    for prev, cur in zip(trend_sorted, trend_sorted[1:]):
        prev_rev = _num(prev.get("total_revenue"))
        cur_rev = _num(cur.get("total_revenue"))
        month_growth.append(
            {
                "from": f"{prev.get('fiscal_year')}-{int(prev.get('fiscal_month', 0)):02d}",
                "to": f"{cur.get('fiscal_year')}-{int(cur.get('fiscal_month', 0)):02d}",
                "growth_pct": (cur_rev - prev_rev) / prev_rev if prev_rev else None,
                "revenue_delta": cur_rev - prev_rev,
            }
        )

    top_geo = _top(geo_rows, "total_revenue", 8)
    top_products = _top(product_rows, "total_revenue", 8)
    top_customers = _top(customer_rows, "monetary", 12)
    stale_high_value = [
        row for row in customer_rows
        if _num(row.get("monetary")) > 0 and _num(row.get("recency_days")) >= 30
    ][:12]

    demand = forecast_result.get("forecast_results", {}).get("demand", {})
    colors = forecast_result.get("forecast_results", {}).get("colors", {})
    churn = forecast_result.get("churn_results", {})
    group_forecast = demand.get("group_forecast", [])

    forecast_total = _num(demand.get("summary", {}).get("q2_total_revenue_forecast"))
    base_q1 = total_revenue
    forecast_vs_q1 = (forecast_total - base_q1) / base_q1 if base_q1 else None
    high_risk_count = _num(churn.get("summary", {}).get("high_risk_count"))
    customer_count = _num(churn.get("summary", {}).get("customer_count"))

    return {
        "business_window": {"date_from": bi_result.get("date_from"), "date_to": bi_result.get("date_to")},
        "kpis": {
            "revenue": total_revenue,
            "orders": _num(kpi.get("order_count")),
            "active_dealers": _num(kpi.get("active_customer_count")),
            "active_skus": _num(kpi.get("active_sku_count")),
            "aov": _num(kpi.get("avg_order_value")),
            "avg_lines_per_order": _num(kpi.get("avg_lines_per_order")),
        },
        "trend_decomposition": {
            "monthly_revenue": trend_sorted,
            "month_growth": month_growth,
        },
        "portfolio_signals": {
            "top_product_groups": top_products,
            "unknown_group_revenue_share": unknown_share,
            "unknown_group_revenue": _num(unknown_product.get("total_revenue")),
        },
        "geography_signals": {
            "top_geographies": top_geo,
            "top_geo_concentration_share": sum(_num(r.get("total_revenue")) for r in top_geo[:3]) / total_revenue if total_revenue else 0,
        },
        "dealer_behavior_signals": {
            "top_customers": top_customers,
            "stale_high_value_customers": stale_high_value,
            "high_risk_dealer_count": high_risk_count,
            "dealer_count": customer_count,
            "high_risk_dealer_share": high_risk_count / customer_count if customer_count else 0,
            "top_priority_dealers": churn.get("summary", {}).get("top_priority_dealers", []),
        },
        "forecast_signals": {
            "q2_total_revenue_forecast": forecast_total,
            "forecast_vs_q1_pct": forecast_vs_q1,
            "methods": demand.get("summary", {}).get("methods", []),
            "group_forecast": group_forecast,
            "scenario_summary": demand.get("scenario_summary", {}),
            "sensitivity": demand.get("sensitivity", {}),
        },
        "color_signals": {
            "rising_colors": colors.get("rising_colors", [])[:8],
            "declining_colors": colors.get("declining_colors", [])[:8],
            "top_colors": colors.get("top_colors", [])[:8],
        },
        "data_quality_meta": {
            "unknown_group_revenue_share": unknown_share,
            "unknown_group_is_material": unknown_share >= 0.05,
            "forecast_method_warning": "rolling_trend_fallback" in demand.get("summary", {}).get("methods", []),
        },
    }


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _compact(payload: Any, max_chars: int = 18000) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)[:max_chars]


def _executive_digest(signals: dict[str, Any]) -> dict[str, Any]:
    return {
        "business_window": signals.get("business_window", {}),
        "kpis": signals.get("kpis", {}),
        "monthly_growth": signals.get("trend_decomposition", {}).get("month_growth", [])[-3:],
        "top_product_groups": signals.get("portfolio_signals", {}).get("top_product_groups", [])[:5],
        "unknown_group": {
            "revenue_share": signals.get("portfolio_signals", {}).get("unknown_group_revenue_share"),
            "revenue": signals.get("portfolio_signals", {}).get("unknown_group_revenue"),
            "is_material": signals.get("data_quality_meta", {}).get("unknown_group_is_material"),
        },
        "top_geographies": signals.get("geography_signals", {}).get("top_geographies", [])[:5],
        "geo_concentration_share": signals.get("geography_signals", {}).get("top_geo_concentration_share"),
        "dealer_risk": signals.get("dealer_behavior_signals", {}),
        "forecast": {
            "q2_total_revenue_forecast": signals.get("forecast_signals", {}).get("q2_total_revenue_forecast"),
            "forecast_vs_q1_pct": signals.get("forecast_signals", {}).get("forecast_vs_q1_pct"),
            "methods": signals.get("forecast_signals", {}).get("methods", []),
            "scenario_summary": signals.get("forecast_signals", {}).get("scenario_summary", {}),
            "sensitivity": signals.get("forecast_signals", {}).get("sensitivity", {}),
            "top_group_forecast": signals.get("forecast_signals", {}).get("group_forecast", [])[:5],
        },
        "colors": {
            "rising": signals.get("color_signals", {}).get("rising_colors", [])[:5],
            "declining": signals.get("color_signals", {}).get("declining_colors", [])[:5],
        },
        "data_quality": signals.get("data_quality_meta", {}),
    }


def _fallback_strategy(signals: dict[str, Any], reason: str) -> dict[str, Any]:
    kpis = signals.get("kpis", {})
    forecast = signals.get("forecast_signals", {})
    dealer = signals.get("dealer_behavior_signals", {})
    portfolio = signals.get("portfolio_signals", {})
    unknown_share = portfolio.get("unknown_group_revenue_share", 0)
    return {
        "status": "STRATEGIC_FALLBACK",
        "reason": reason,
        "executive_narrative": {
            "business_situation": f"Q1 revenue reached {_num(kpis.get('revenue')):,.0f} VND with {_num(kpis.get('active_dealers')):,.0f} active dealers.",
            "hidden_patterns": [
                "Revenue concentration and churn risk must be interpreted together, not as separate BI views.",
                f"Unknown product mapping accounts for {unknown_share:.1%} of revenue, creating blind spots in portfolio decisions.",
            ],
            "strategic_risks": [
                f"High-risk dealer share is {_num(dealer.get('high_risk_dealer_share')):.1%}; revenue growth can leak through dealer inactivity.",
                "Forecast confidence is limited by short seasonal history, so scenario planning is more defensible than single-point forecast.",
            ],
            "growth_opportunities": [
                "Use top product groups and rising colors to build focused bundles for high-value dealer cohorts.",
                "Treat Unknown category cleanup as a revenue-protection initiative before SKU rationalization decisions.",
            ],
            "prioritized_actions": [
                "Launch a 30-day retention sprint for the top priority dealers before expanding acquisition spend.",
                "Resolve Unknown product mapping and re-run product lifecycle analysis before inventory commitments.",
            ],
        },
        "strategic_insights": [
            {
                "title": "Growth is exposed to dealer retention leakage",
                "what_is_happening": "Q2 forecast implies growth, while churn model flags a material high-risk dealer base.",
                "why_it_is_happening": "Dealer purchase behavior is uneven; high monetary customers can still show stale recency.",
                "quantitative_evidence": [
                    f"High-risk dealers: {_num(dealer.get('high_risk_dealer_count')):,.0f}/{_num(dealer.get('dealer_count')):,.0f}",
                    f"Q2 forecast: {_num(forecast.get('q2_total_revenue_forecast')):,.0f} VND",
                ],
                "benchmark_comparison": "Compared with a healthy growth pattern, forecast growth is not matched by uniformly active dealer cohorts.",
                "root_cause_reasoning": "Revenue momentum is likely pulled by a subset of active/high-value dealers while inactive or stale dealers widen the loyalty gap.",
                "long_term_impact": "If unresolved, revenue becomes more concentrated and less repeatable across quarters.",
                "risk_if_ignored": "Sales targets may be met short term but CAC and reactivation cost rise later.",
                "optimal_lever": "Dealer cohort retention and reactivation sequencing.",
                "highest_roi_action": "Prioritize top-risk high-value dealers with bundle and credit/service interventions.",
                "strategic_implication": "Retention should be managed as a growth lever, not a customer-service afterthought.",
                "confidence": "medium",
            },
            {
                "title": "Unknown portfolio share is a strategic blind spot",
                "what_is_happening": "A material share of revenue is not cleanly mapped to product group lifecycle logic.",
                "why_it_is_happening": "Master-data mapping gaps hide true SKU/category performance.",
                "quantitative_evidence": [f"Unknown revenue share: {unknown_share:.1%}"],
                "benchmark_comparison": "For executive product decisions, unknown category share should be immaterial; above 5% is a governance risk.",
                "root_cause_reasoning": "Analytics can overstate or understate category winners if unmapped revenue is large.",
                "long_term_impact": "Inventory and assortment decisions may reinforce the wrong product lines.",
                "risk_if_ignored": "SKU rationalization and forecast allocation can shift capital into misclassified demand.",
                "optimal_lever": "Master-data remediation before portfolio pruning.",
                "highest_roi_action": "Resolve Unknown mapping and re-run lifecycle/margin analysis.",
                "strategic_implication": "Data quality is a business risk because it directly affects inventory and category bets.",
                "confidence": "high" if unknown_share >= 0.05 else "medium",
            },
        ],
        "scenario_forecast": forecast.get("scenario_summary", {}),
        "data_quality_assessment": signals.get("data_quality_meta", {}),
        "recommendations": [
            {
                "priority": "P1",
                "recommendation": "Dealer retention sprint for high-risk/high-value customers",
                "prioritization_score": 92,
                "expected_business_impact": "Protect Q2 forecast base and reduce revenue concentration risk.",
                "implementation_complexity": "medium",
                "estimated_roi": "high",
                "execution_dependency": "Sales owner list, dealer call scripts, bundle/credit guardrails.",
                "timeline": "First 30 days of Q2",
            },
            {
                "priority": "P1",
                "recommendation": "Clean Unknown product group mapping before inventory decisions",
                "prioritization_score": 88,
                "expected_business_impact": "Improve forecast allocation and SKU rationalization quality.",
                "implementation_complexity": "low-medium",
                "estimated_roi": "medium-high",
                "execution_dependency": "Product master owner and ERP mapping rules.",
                "timeline": "2 weeks",
            },
        ],
        "self_critique": {
            "removed_low_value_insights": [],
            "confidence_notes": ["Fallback strategy used because LLM synthesis was unavailable."],
        },
    }


def _quality_issues(report: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    insights = report.get("strategic_insights", [])
    if len(insights) < 3:
        issues.append("Fewer than 3 strategic insights.")

    forbidden_fragments = [
        "do nhu cầu",
        "nhu cầu tăng cao",
        "tăng cường quảng cáo",
        "khuyến mãi",
        "mất thị phần và doanh thu",
        "chất lượng và giá cả cạnh tranh",
    ]
    required_fields = [
        "what_is_happening",
        "why_it_is_happening",
        "quantitative_evidence",
        "benchmark_comparison",
        "root_cause_reasoning",
        "long_term_impact",
        "risk_if_ignored",
        "optimal_lever",
        "highest_roi_action",
        "strategic_implication",
    ]
    for idx, insight in enumerate(insights, start=1):
        for field in required_fields:
            if not insight.get(field):
                issues.append(f"Insight {idx} missing {field}.")
        joined = " ".join(str(insight.get(field, "")) for field in required_fields).lower()
        if any(fragment in joined for fragment in forbidden_fragments):
            issues.append(f"Insight {idx} contains generic/unsupported reasoning or recommendation.")
        evidence = insight.get("quantitative_evidence", [])
        evidence_text = " ".join(str(x) for x in evidence)
        if not any(char.isdigit() for char in evidence_text):
            issues.append(f"Insight {idx} lacks numeric evidence.")

    for idx, rec in enumerate(report.get("recommendations", []), start=1):
        if rec.get("prioritization_score") in (None, ""):
            issues.append(f"Recommendation {idx} missing prioritization_score.")
        for field in ["expected_business_impact", "implementation_complexity", "estimated_roi", "execution_dependency", "timeline"]:
            if not rec.get(field):
                issues.append(f"Recommendation {idx} missing {field}.")
    return issues


def generate_strategic_intelligence(
    bi_result: dict[str, Any],
    forecast_result: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    signals = extract_statistical_signals(bi_result, forecast_result)
    if dry_run:
        return _fallback_strategy(signals, "DRY_RUN")

    client = GroqKeyPoolClient()
    if not client.has_keys():
        reason = "GROQ_API_KEYS is missing; strategic intelligence synthesis skipped."
        log_pending_issue(reason)
        return _fallback_strategy(signals, reason)

    try:
        client.max_tokens = min(client.max_tokens, 800)
        synthesis_prompt = f"""
Run this multi-step pipeline internally, then output only final JSON:
1. Statistical signal extraction.
2. Hypothesis generation.
3. Contradiction checking.
4. Business interpretation.
5. Executive recommendation synthesis.
6. Self-critique: remove obvious, repeated or non-actionable insights.

Signals:
{_compact(_executive_digest(signals), 4200)}

Create a consulting-style Strategic Executive Intelligence Report.
Return JSON with this exact shape:
{{
  "status": "SUCCESS",
  "executive_narrative": {{
    "business_situation": "...",
    "hidden_patterns": ["..."],
    "strategic_risks": ["..."],
    "growth_opportunities": ["..."],
    "prioritized_actions": ["..."]
  }},
  "strategic_insights": [
    {{
      "title": "...",
      "what_is_happening": "...",
      "why_it_is_happening": "...",
      "quantitative_evidence": ["..."],
      "benchmark_comparison": "...",
      "root_cause_reasoning": "...",
      "long_term_impact": "...",
      "risk_if_ignored": "...",
      "optimal_lever": "...",
      "highest_roi_action": "...",
      "strategic_implication": "...",
      "confidence": "high/medium/low"
    }}
  ],
  "scenario_forecast": {{
    "base": "...",
    "optimistic": "...",
    "pessimistic": "...",
    "confidence_interval_explanation": "...",
    "key_drivers": ["..."],
    "sensitivity_analysis": ["..."]
  }},
  "data_quality_assessment": {{...}},
  "recommendations": [
    {{
      "priority": "P1/P2/P3",
      "recommendation": "...",
      "prioritization_score": 0,
      "expected_business_impact": "...",
      "implementation_complexity": "low/medium/high",
      "estimated_roi": "...",
      "execution_dependency": "...",
      "timeline": "..."
    }}
  ],
  "self_critique": {{
    "removed_low_value_insights": ["..."],
    "confidence_notes": ["..."]
  }}
}}
"""
        strategic = client.chat_json(synthesis_prompt, system=STRATEGY_SYSTEM_PROMPT)
        strategic.setdefault("status", "SUCCESS")

        issues = _quality_issues(strategic)
        if issues:
            repair_prompt = f"""
STEP 4 - Quality gate repair.
The draft report failed these quality checks:
{_compact(issues)}

Original draft:
{_compact(strategic, 16000)}

Signals:
{_compact(_executive_digest(signals), 4200)}

Rewrite the report to fix all issues. Requirements:
- Replace generic causal claims with cross-dimensional mechanisms.
- Use numbers in every insight.
- Add contradiction detection, risk propagation, opportunity sizing and data-quality meta-analysis.
- Keep only non-obvious, strategically actionable insights.
- Do not recommend generic advertising/promotion unless tied to a specific cohort/product/geography and ROI mechanism.
- Return the same JSON shape as STEP 3.
"""
            repaired = client.chat_json(repair_prompt, system=STRATEGY_SYSTEM_PROMPT)
            repaired.setdefault("status", "SUCCESS")
            repaired.setdefault("self_critique", {})
            repaired["self_critique"]["quality_gate_repairs"] = issues
            strategic = repaired
            issues = _quality_issues(strategic)

        if issues:
            fallback = _fallback_strategy(
                signals,
                "LLM output failed strategic quality gate; using deterministic strategic fallback.",
            )
            fallback.setdefault("self_critique", {})
            fallback["self_critique"]["quality_gate_failures"] = issues
            fallback["statistical_signals"] = signals
            return fallback

        strategic["statistical_signals"] = signals
        return strategic
    except Exception as exc:
        reason = f"Strategic intelligence LLM pipeline failed: {exc}"
        logger.warning(reason)
        log_pending_issue(reason)
        fallback = _fallback_strategy(signals, reason)
        fallback["statistical_signals"] = signals
        return fallback
