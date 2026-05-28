"""Render TNBIKE AI reports as HTML and Markdown."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ai.common import AI_ROOT, setup_logging


logger = setup_logging(__name__)
TEMPLATE_DIR = AI_ROOT / "report" / "templates"
DEFAULT_OUTPUT_DIR = AI_ROOT / "report" / "output"


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def format_money(value: Any) -> str:
    return f"{_num(value):,.0f} VND"


def format_number(value: Any) -> str:
    return f"{_num(value):,.0f}"


def format_pct(value: Any) -> str:
    return f"{_num(value) * 100:.1f}%"


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ["action", "recommendation", "title", "summary", "description", "risk", "opportunity"]:
            if value.get(key):
                return str(value[key])
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _join_items(values: Any) -> str:
    if not values:
        return ""
    if isinstance(values, (str, dict)):
        values = [values]
    return "; ".join(_stringify(value) for value in values)


def _first_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return rows[0] if rows else {}


def _format_product_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows[:12]:
        item = dict(row)
        item["group_name"] = item.get("group_name") or "Unknown"
        item["total_revenue_fmt"] = format_money(item.get("total_revenue"))
        item["total_qty_fmt"] = format_number(item.get("total_qty"))
        item["revenue_share_fmt"] = format_pct(item.get("revenue_share"))
        out.append(item)
    return out


def _format_forecast_group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows[:12]:
        item = dict(row)
        item["group_name"] = item.get("group_name") or "Unknown"
        item["forecast_revenue_q2_fmt"] = format_money(item.get("forecast_revenue_q2"))
        item["forecast_qty_q2_fmt"] = format_number(item.get("forecast_qty_q2"))
        item["lower_revenue_q2_fmt"] = format_money(item.get("lower_revenue_q2"))
        item["upper_revenue_q2_fmt"] = format_money(item.get("upper_revenue_q2"))
        out.append(item)
    return out


def build_report_context(
    bi_result: dict[str, Any],
    forecast_result: dict[str, Any],
    strategic_result: dict[str, Any] | None,
    generated_at: str,
) -> dict[str, Any]:
    product_rows = bi_result.get("data", {}).get("product_analysis", [])
    operational_rows = bi_result.get("data", {}).get("operational_kpis", [])
    metrics = _first_row(operational_rows)
    forecast_summary = forecast_result.get("forecast_results", {}).get("demand", {}).get("summary", {})
    forecast_groups = forecast_result.get("forecast_results", {}).get("demand", {}).get("group_forecast", [])
    churn_summary = forecast_result.get("churn_results", {}).get("summary", {})
    reasoning = forecast_result.get("reasoning", {})
    strategic = strategic_result or {}
    scenario = forecast_result.get("forecast_results", {}).get("demand", {}).get("scenario_summary", {})
    sensitivity = forecast_result.get("forecast_results", {}).get("demand", {}).get("sensitivity", {})

    return {
        "generated_at": generated_at,
        "date_from": bi_result.get("date_from", ""),
        "date_to": bi_result.get("date_to", ""),
        "dry_run": bi_result.get("dry_run") or forecast_result.get("dry_run"),
        "metrics": {
            "order_count": format_number(metrics.get("order_count")),
            "active_customer_count": format_number(metrics.get("active_customer_count")),
            "total_revenue": format_money(metrics.get("total_revenue")),
            "avg_order_value": format_money(metrics.get("avg_order_value")),
        },
        "bi_interpretation": bi_result.get("interpretation", {}),
        "product_rows": _format_product_rows(product_rows),
        "forecast_metrics": {
            "q2_total_revenue_forecast": format_money(forecast_summary.get("q2_total_revenue_forecast")),
            "methods": ", ".join(forecast_summary.get("methods", [])) or "n/a",
        },
        "forecast_group_rows": _format_forecast_group_rows(forecast_groups),
        "churn_summary": {
            "high_risk_count": format_number(churn_summary.get("high_risk_count")),
            "customer_count": format_number(churn_summary.get("customer_count")),
        },
        "reasoning": {
            "color_strategy": {
                "rising_colors": reasoning.get("color_strategy", {}).get("rising_colors", []),
                "declining_colors": reasoning.get("color_strategy", {}).get("declining_colors", []),
                "recommendation": reasoning.get("color_strategy", {}).get("recommendation", ""),
            },
            "dealer_actions": {
                "retention_priority": reasoning.get("dealer_actions", {}).get("retention_priority", []),
                "strategy": reasoning.get("dealer_actions", {}).get("strategy", ""),
            },
            "strategic_recommendations": reasoning.get("strategic_recommendations", []),
        },
        "strategic": strategic,
        "executive_narrative": strategic.get("executive_narrative", {}),
        "strategic_insights": strategic.get("strategic_insights", []),
        "strategic_recommendations": strategic.get("recommendations", []),
        "data_quality_assessment": strategic.get("data_quality_assessment", {}),
        "self_critique": strategic.get("self_critique", {}),
        "scenario": strategic.get("scenario_forecast") or {
            "base": format_money(scenario.get("base_revenue") or forecast_summary.get("q2_total_revenue_forecast")),
            "optimistic": format_money(scenario.get("optimistic_revenue")),
            "pessimistic": format_money(scenario.get("pessimistic_revenue")),
            "confidence_interval_explanation": scenario.get("confidence_interval_explanation", ""),
            "key_drivers": forecast_summary.get("key_drivers", []),
            "sensitivity_analysis": [
                f"Price +5%: {format_money(sensitivity.get('price_plus_5pct_revenue_delta'))}",
                f"Volume +10%: {format_money(sensitivity.get('volume_plus_10pct_revenue_delta'))}",
                f"Dealer retention +5% proxy: {format_money(sensitivity.get('dealer_retention_plus_5pct_proxy'))}",
            ],
        },
    }


def render_html(context: dict[str, Any]) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("report.html.j2")
    return template.render(**context)


def render_markdown(context: dict[str, Any]) -> str:
    product_lines = [
        f"- {row['group_name']}: {row['total_revenue_fmt']} revenue, {row['total_qty_fmt']} units"
        for row in context["product_rows"]
    ]
    forecast_lines = [
        f"- {row['group_name']}: {row['forecast_revenue_q2_fmt']} ({row['lower_revenue_q2_fmt']} - {row['upper_revenue_q2_fmt']})"
        for row in context["forecast_group_rows"]
    ]
    insights = context.get("strategic_insights") or []
    insight_lines = []
    for item in insights:
        if "what_is_happening" in item:
            evidence = item.get("quantitative_evidence", [])
            insight_lines.extend(
                [
                    f"### {item.get('title', 'Strategic insight')}",
                    f"- What is happening: {item.get('what_is_happening', '')}",
                    f"- Why it is happening: {item.get('why_it_is_happening', '')}",
                    f"- Evidence: {'; '.join(str(x) for x in evidence)}",
                    f"- Benchmark: {item.get('benchmark_comparison', '')}",
                    f"- Root cause: {item.get('root_cause_reasoning', '')}",
                    f"- Long-term impact: {item.get('long_term_impact', '')}",
                    f"- Risk if ignored: {item.get('risk_if_ignored', '')}",
                    f"- Optimal lever: {item.get('optimal_lever', '')}",
                    f"- Highest ROI action: {item.get('highest_roi_action', '')}",
                    f"- Strategic implication: {item.get('strategic_implication', '')}",
                ]
            )
            continue
        insight_lines.extend(
            [
                f"### {item.get('title', 'Insight')}",
                f"- Finding: {item.get('finding', '')}",
                f"- Impact: {item.get('business_impact', '')}",
                f"- Action: {item.get('action', '')}",
            ]
        )
    if not insight_lines:
        insight_lines = [
            "### LLM interpretation unavailable",
            "- Finding: BI data was extracted successfully, but narrative insights were not generated.",
            "- Impact: Report remains usable for operational review with model outputs and tables.",
            "- Action: Add GROQ_API_KEYS and rerun without --dry-run for full interpretation.",
        ]

    return "\n".join(
        [
            "# TNBIKE AI Business Report",
            f"Generated: {context['generated_at']}",
            f"Window: {context['date_from']} to {context['date_to']}",
            "",
            "## BI & Operational Insights",
            f"- Orders: {context['metrics']['order_count']}",
            f"- Active dealers: {context['metrics']['active_customer_count']}",
            f"- Revenue: {context['metrics']['total_revenue']}",
            f"- AOV: {context['metrics']['avg_order_value']}",
            "",
            "## Executive Narrative",
            f"- Business situation: {context.get('executive_narrative', {}).get('business_situation', '')}",
            f"- Hidden patterns: {_join_items(context.get('executive_narrative', {}).get('hidden_patterns', []))}",
            f"- Strategic risks: {_join_items(context.get('executive_narrative', {}).get('strategic_risks', []))}",
            f"- Growth opportunities: {_join_items(context.get('executive_narrative', {}).get('growth_opportunities', []))}",
            f"- Prioritized actions: {_join_items(context.get('executive_narrative', {}).get('prioritized_actions', []))}",
            "",
            "## Strategic Insights",
            *insight_lines,
            "",
            "## Product Group Snapshot",
            *product_lines,
            "",
            "## Predictive Results & Strategic Insights",
            f"- Q2 revenue forecast: {context['forecast_metrics']['q2_total_revenue_forecast']}",
            f"- Forecast method: {context['forecast_metrics']['methods']}",
            f"- High-risk dealers: {context['churn_summary']['high_risk_count']}",
            "",
            "## Scenario Forecast",
            f"- Pessimistic: {context.get('scenario', {}).get('pessimistic', '')}",
            f"- Base: {context.get('scenario', {}).get('base', '')}",
            f"- Optimistic: {context.get('scenario', {}).get('optimistic', '')}",
            f"- Confidence: {context.get('scenario', {}).get('confidence_interval_explanation', '')}",
            f"- Drivers: {_join_items(context.get('scenario', {}).get('key_drivers', []))}",
            f"- Sensitivity: {_join_items(context.get('scenario', {}).get('sensitivity_analysis', []))}",
            "",
            "## Q2 Forecast By Product Group",
            *forecast_lines,
            "",
            "## Color Strategy",
            f"- Rising: {', '.join(context['reasoning']['color_strategy']['rising_colors'])}",
            f"- Declining: {', '.join(context['reasoning']['color_strategy']['declining_colors'])}",
            f"- Recommendation: {context['reasoning']['color_strategy']['recommendation']}",
            "",
            "## Dealer Actions",
            f"- Retention priority: {', '.join(context['reasoning']['dealer_actions']['retention_priority'])}",
            f"- Strategy: {context['reasoning']['dealer_actions']['strategy']}",
            "",
            "## Prioritized Strategic Recommendations",
            *[
                f"- {item.get('priority', '')} | score {item.get('prioritization_score', '')}: {item.get('recommendation', '')} | impact: {item.get('expected_business_impact', '')} | complexity: {item.get('implementation_complexity', '')} | ROI: {item.get('estimated_roi', '')} | dependency: {item.get('execution_dependency', '')} | timeline: {item.get('timeline', '')}"
                for item in context.get("strategic_recommendations", [])
            ],
            "",
            "## Data Confidence & Self-Critique",
            f"- Data quality: {json.dumps(context.get('data_quality_assessment', {}), ensure_ascii=False, default=str)}",
            f"- Removed low-value insights: {_join_items(context.get('self_critique', {}).get('removed_low_value_insights', []))}",
            f"- Confidence notes: {_join_items(context.get('self_critique', {}).get('confidence_notes', []))}",
            "",
        ]
    )


def generate_report(
    bi_result: dict[str, Any],
    forecast_result: dict[str, Any],
    strategic_result: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, str]:
    output_path = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    output_path.mkdir(parents=True, exist_ok=True)
    generated_dt = datetime.now()
    generated_at = generated_dt.strftime("%Y-%m-%d %H:%M:%S")
    stamp = generated_dt.strftime("%Y%m%d_%H%M%S_%f")

    context = build_report_context(bi_result, forecast_result, strategic_result, generated_at)
    html = render_html(context)
    markdown = render_markdown(context)

    html_path = output_path / f"report_{stamp}.html"
    markdown_path = output_path / f"report_{stamp}.md"
    html_path.write_text(html, encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")

    logger.info("Generated report: %s", html_path)
    return {
        "html": str(html_path),
        "markdown": str(markdown_path),
        "generated_at": generated_at,
    }
