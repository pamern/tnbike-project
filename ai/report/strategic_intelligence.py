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
Toàn bộ giá trị văn bản hiển thị cho người dùng phải viết bằng tiếng Việt thuần túy, chuyên nghiệp.

Quy tắc bắt buộc:
- Không viết insight theo template nông "Finding/Impact/Action" nếu không có causal reasoning.
- Mỗi insight phải trả lời: điều gì xảy ra, vì sao, tác động dài hạn, hậu quả nếu không xử lý, đòn bẩy tối ưu, action ROI cao nhất.
- Luôn so sánh đa chiều giữa geography, dealer cohort, product lifecycle, seasonality, margin/price structure, churn risk, inventory velocity proxy và data quality.
- Phải gọi rõ quantitative evidence, benchmark comparison, root-cause reasoning, strategic implication.
- Phát hiện mâu thuẫn: ví dụ doanh thu tăng nhưng loyalty/churn xấu; danh mục tăng trưởng nhưng biên lợi nhuận hoặc ASP yếu; dự báo tăng nhưng chất lượng dữ liệu kém.
- Meta-analysis là bắt buộc: tự đánh giá độ tin cậy dữ liệu và coi vùng chưa định danh hoặc thiếu mapping là rủi ro kinh doanh nếu có ý nghĩa vật chất.
- Recommendation phải có prioritization_score, expected_business_impact, implementation_complexity, estimated_roi, execution_dependency, timeline.
- Insight kém là insight hiển nhiên, chỉ nhắc lại aggregation, hoặc không actionable. Loại bỏ chúng trong self-critique.
- Cấm dùng nguyên nhân vòng lặp như "tăng vì nhu cầu tăng", "do khách hàng thích", "quảng cáo nhiều hơn" nếu không có cơ chế vận hành hoặc dữ liệu hỗ trợ.
- Cấm recommendation chung chung như "tăng quảng cáo/khuyến mãi" nếu không có cohort, SKU, geography, dependency và ROI logic.
- Nếu evidence không đủ để khẳng định nguyên nhân, phải nói là "hypothesis" và nêu cách kiểm chứng.
- Chỉ trả JSON hợp lệ; giữ tên key JSON theo schema, nhưng mọi giá trị nội dung phải là tiếng Việt.
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
            "business_situation": f"Doanh thu Q1 đạt {_num(kpis.get('revenue')):,.0f} VND với {_num(kpis.get('active_dealers')):,.0f} đại lý đang hoạt động.",
            "hidden_patterns": [
                "Tập trung doanh thu và rủi ro rời bỏ cần được đọc cùng nhau, không nên tách thành hai góc nhìn BI độc lập.",
                f"Nhóm sản phẩm chưa định danh chiếm {unknown_share:.1%} doanh thu, tạo điểm mù trong quyết định danh mục.",
            ],
            "strategic_risks": [
                f"Tỷ lệ đại lý rủi ro cao là {_num(dealer.get('high_risk_dealer_share')):.1%}; tăng trưởng doanh thu có thể thất thoát qua nhóm đại lý giảm hoạt động.",
                "Độ tin cậy dự báo bị giới hạn bởi lịch sử mùa vụ ngắn, vì vậy lập kịch bản đáng tin cậy hơn một con số dự báo đơn lẻ.",
            ],
            "growth_opportunities": [
                "Kết hợp nhóm sản phẩm chủ lực và màu đang tăng để tạo bundle tập trung cho nhóm đại lý giá trị cao.",
                "Xem việc làm sạch nhóm chưa định danh như một sáng kiến bảo vệ doanh thu trước khi tinh gọn SKU.",
            ],
            "prioritized_actions": [
                "Triển khai chiến dịch giữ chân 30 ngày cho nhóm đại lý ưu tiên trước khi tăng ngân sách mở rộng.",
                "Xử lý mapping sản phẩm chưa định danh và chạy lại phân tích vòng đời sản phẩm trước khi cam kết tồn kho.",
            ],
        },
        "strategic_insights": [
            {
                "title": "Tăng trưởng đang hở qua rủi ro giữ chân đại lý",
                "what_is_happening": "Dự báo Q2 cho thấy tăng trưởng, trong khi mô hình rời bỏ vẫn đánh dấu một nhóm đại lý rủi ro cao đáng kể.",
                "why_it_is_happening": "Hành vi mua hàng của đại lý không đồng đều; khách hàng có doanh thu cao vẫn có thể đã lâu chưa quay lại.",
                "quantitative_evidence": [
                    f"Đại lý rủi ro cao: {_num(dealer.get('high_risk_dealer_count')):,.0f}/{_num(dealer.get('dealer_count')):,.0f}",
                    f"Dự báo Q2: {_num(forecast.get('q2_total_revenue_forecast')):,.0f} VND",
                ],
                "benchmark_comparison": "So với một mô hình tăng trưởng khỏe, tăng trưởng dự báo chưa đi kèm mức hoạt động đồng đều giữa các nhóm đại lý.",
                "root_cause_reasoning": "Đà doanh thu có thể đang được kéo bởi một nhóm đại lý còn hoạt động và giá trị cao, trong khi đại lý ngừng mua làm rộng thêm khoảng trống loyalty.",
                "long_term_impact": "Nếu không xử lý, doanh thu sẽ tập trung hơn và kém lặp lại qua các quý.",
                "risk_if_ignored": "Mục tiêu ngắn hạn có thể vẫn đạt, nhưng chi phí kích hoạt lại và chi phí mở rộng sẽ tăng về sau.",
                "optimal_lever": "Giữ chân theo cohort đại lý và sắp xếp ưu tiên kích hoạt lại.",
                "highest_roi_action": "Ưu tiên đại lý giá trị cao nhưng rủi ro lớn bằng bundle và chính sách tín dụng/dịch vụ phù hợp.",
                "strategic_implication": "Giữ chân cần được quản trị như một đòn bẩy tăng trưởng, không phải phần việc hậu mãi.",
                "confidence": "medium",
            },
            {
                "title": "Tỷ trọng danh mục chưa định danh là điểm mù chiến lược",
                "what_is_happening": "Một phần doanh thu đáng kể chưa được gắn rõ vào logic vòng đời nhóm sản phẩm.",
                "why_it_is_happening": "Khoảng trống mapping dữ liệu chủ làm che khuất hiệu quả thật của SKU và danh mục.",
                "quantitative_evidence": [f"Tỷ trọng doanh thu chưa định danh: {unknown_share:.1%}"],
                "benchmark_comparison": "Với quyết định danh mục cấp điều hành, nhóm chưa định danh nên không đáng kể; trên 5% đã là rủi ro quản trị.",
                "root_cause_reasoning": "Phân tích có thể đánh giá quá cao hoặc quá thấp nhóm thắng cuộc nếu doanh thu chưa mapping còn lớn.",
                "long_term_impact": "Quyết định tồn kho và phối danh mục có thể củng cố sai dòng sản phẩm.",
                "risk_if_ignored": "Tinh gọn SKU và phân bổ dự báo có thể đẩy vốn vào nhu cầu bị phân loại sai.",
                "optimal_lever": "Sửa dữ liệu chủ trước khi tinh gọn danh mục.",
                "highest_roi_action": "Xử lý mapping nhóm chưa định danh và chạy lại phân tích vòng đời/biên lợi nhuận.",
                "strategic_implication": "Chất lượng dữ liệu là rủi ro kinh doanh vì ảnh hưởng trực tiếp tới tồn kho và lựa chọn danh mục.",
                "confidence": "high" if unknown_share >= 0.05 else "medium",
            },
        ],
        "scenario_forecast": forecast.get("scenario_summary", {}),
        "data_quality_assessment": signals.get("data_quality_meta", {}),
        "recommendations": [
            {
                "priority": "P1",
                "recommendation": "Chiến dịch giữ chân đại lý giá trị cao có rủi ro rời bỏ",
                "prioritization_score": 92,
                "expected_business_impact": "Bảo vệ nền doanh thu dự báo Q2 và giảm rủi ro tập trung doanh thu.",
                "implementation_complexity": "trung bình",
                "estimated_roi": "cao",
                "execution_dependency": "Danh sách phụ trách bán hàng, kịch bản gọi đại lý, chính sách bundle/tín dụng.",
                "timeline": "30 ngày đầu Q2",
            },
            {
                "priority": "P1",
                "recommendation": "Làm sạch mapping nhóm sản phẩm chưa định danh trước quyết định tồn kho",
                "prioritization_score": 88,
                "expected_business_impact": "Cải thiện phân bổ dự báo và chất lượng quyết định tinh gọn SKU.",
                "implementation_complexity": "thấp đến trung bình",
                "estimated_roi": "trung bình đến cao",
                "execution_dependency": "Người phụ trách dữ liệu sản phẩm và quy tắc mapping ERP.",
                "timeline": "2 tuần",
            },
        ],
        "self_critique": {
            "removed_low_value_insights": [],
            "confidence_notes": ["Đã dùng chiến lược dự phòng vì tổng hợp LLM chưa khả dụng."],
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
            issues.append(f"Khuyến nghị {idx} thiếu prioritization_score.")
        for field in ["expected_business_impact", "implementation_complexity", "estimated_roi", "execution_dependency", "timeline"]:
            if not rec.get(field):
                issues.append(f"Khuyến nghị {idx} thiếu {field}.")
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
        reason = f"{client.provider.upper()} API keys are missing; strategic intelligence synthesis skipped."
        log_pending_issue(reason)
        return _fallback_strategy(signals, reason)

    try:
        client.max_tokens = min(client.max_tokens, 800)
        synthesis_prompt = f"""
Chạy nội bộ quy trình nhiều bước sau, sau đó chỉ trả JSON cuối cùng:
1. Trích xuất tín hiệu thống kê.
2. Tạo giả thuyết.
3. Kiểm tra mâu thuẫn.
4. Diễn giải kinh doanh.
5. Tổng hợp khuyến nghị điều hành.
6. Tự phản biện: loại bỏ insight hiển nhiên, lặp lại hoặc không hành động được.

Signals:
{_compact(_executive_digest(signals), 4200)}

Tạo báo cáo điều hành chiến lược theo phong cách tư vấn.
Tất cả giá trị văn bản phải là tiếng Việt chuyên nghiệp. Trả JSON theo đúng cấu trúc sau:
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
Bước 4 - Sửa lỗi kiểm định chất lượng.
Bản nháp chưa đạt các kiểm tra chất lượng sau:
{_compact(issues)}

Bản nháp gốc:
{_compact(strategic, 16000)}

Signals:
{_compact(_executive_digest(signals), 4200)}

Viết lại báo cáo để sửa toàn bộ lỗi. Yêu cầu:
- Thay các nhận định nguyên nhân chung chung bằng cơ chế đa chiều.
- Dùng số liệu trong mọi insight.
- Bổ sung phát hiện mâu thuẫn, lan truyền rủi ro, định lượng cơ hội và phân tích chất lượng dữ liệu.
- Chỉ giữ insight không hiển nhiên và có thể hành động ở cấp chiến lược.
- Không khuyến nghị quảng cáo/khuyến mãi chung chung nếu không gắn với cohort, SKU, địa lý, phụ thuộc thực thi và logic ROI.
- Tất cả giá trị văn bản phải viết bằng tiếng Việt chuyên nghiệp.
- Trả lại cùng cấu trúc JSON như bước trước.
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
                "Đầu ra LLM không đạt kiểm định chất lượng chiến lược; dùng bản dự phòng xác định.",
            )
            fallback.setdefault("self_critique", {})
            fallback["self_critique"]["quality_gate_failures"] = issues
            fallback["statistical_signals"] = signals
            return fallback

        strategic["statistical_signals"] = signals
        return strategic
    except Exception as exc:
        reason = f"Quy trình tổng hợp chiến lược bằng LLM gặp lỗi: {exc}"
        logger.warning(reason)
        log_pending_issue(reason)
        fallback = _fallback_strategy(signals, reason)
        fallback["statistical_signals"] = signals
        return fallback
