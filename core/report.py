"""Save full analysis results as a markdown report."""
import os
from datetime import datetime

from core.models import FullAnalysis


def _fmt_market_cap(value: float | None) -> str:
    if value is None:
        return "N/A"
    millions = value / 1_000_000
    return f"${millions:,.1f}M"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def _fmt_score(value: float | None, max_val: int = 10) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}/{max_val}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1%}"


def _filter_status(result) -> str:
    if result is None:
        return "NOT REACHED"
    return "PASS" if result.passed else "FAIL"


def save_report(analysis: FullAnalysis, output_dir: str = "reports") -> str:
    """Save analysis as markdown. Returns the file path."""
    os.makedirs(output_dir, exist_ok=True)

    lines = []
    company = analysis.company
    ticker = company.ticker

    # Header
    lines.append(f"# {ticker} — {company.name}")
    lines.append("")
    date_str = analysis.analyzed_at.strftime("%Y-%m-%d") if analysis.analyzed_at else datetime.now().strftime("%Y-%m-%d")
    lines.append(f"**Date:** {date_str}  ")
    lines.append(f"**Price:** {_fmt_price(company.price)} | **Market Cap:** {_fmt_market_cap(company.market_cap)}  ")
    final_text = "PASSED ALL FILTERS" if analysis.final_passed else "Did not pass all filters"
    lines.append(f"**Final Result:** {final_text}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Filter 1: Business Type
    f1_status = _filter_status(analysis.f1_result)
    lines.append(f"## Filter 1: Business Type — {f1_status}")
    lines.append("")
    if analysis.f1_result:
        if analysis.f1_result.reasoning:
            lines.append(analysis.f1_result.reasoning)
            lines.append("")
    else:
        lines.append("(Filter was not executed)")
        lines.append("")
    lines.append("---")
    lines.append("")

    # Filter 2: Management Quality
    f2_status = _filter_status(analysis.f2_result)
    score_suffix = ""
    if analysis.f2_result and analysis.f2_result.score is not None:
        score_suffix = f" ({analysis.f2_result.score:.1f}/100)"
    lines.append(f"## Filter 2: Management Quality — {f2_status}{score_suffix}")
    lines.append("")
    if analysis.f2_result:
        if analysis.f2_scores:
            lines.append("| Dimension | Score |")
            lines.append("|-----------|-------|")
            lines.append(f"| Business Clarity | {_fmt_score(analysis.f2_scores.business_clarity)} |")
            lines.append(f"| Risk Honesty | {_fmt_score(analysis.f2_scores.risk_honesty)} |")
            lines.append(f"| MD&A Transparency | {_fmt_score(analysis.f2_scores.mda_transparency)} |")
            lines.append(f"| KPI Quality | {_fmt_score(analysis.f2_scores.kpi_quality)} |")
            lines.append(f"| Tone Authenticity | {_fmt_score(analysis.f2_scores.tone_authenticity)} |")
            lines.append("")
        if analysis.f2_result.reasoning:
            lines.append("### Reasoning")
            lines.append("")
            lines.append(analysis.f2_result.reasoning)
            lines.append("")
    else:
        lines.append("(Filter was not executed)")
        lines.append("")
    lines.append("---")
    lines.append("")

    # Filter 3: Valuation
    f3_status = _filter_status(analysis.f3_result)
    lines.append(f"## Filter 3: Valuation — {f3_status}")
    lines.append("")
    if analysis.f3_result:
        if analysis.f3_valuation:
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            if analysis.f3_valuation.intrinsic_value is not None:
                lines.append(f"| Intrinsic Value | {_fmt_price(analysis.f3_valuation.intrinsic_value)} |")
            if analysis.f3_valuation.current_price is not None:
                lines.append(f"| Current Price | {_fmt_price(analysis.f3_valuation.current_price)} |")
            if analysis.f3_valuation.margin_of_safety is not None:
                lines.append(f"| Margin of Safety | {_fmt_pct(analysis.f3_valuation.margin_of_safety)} |")
            if analysis.f3_valuation.moat_type:
                strength = _fmt_score(analysis.f3_valuation.moat_strength) if analysis.f3_valuation.moat_strength is not None else "N/A"
                lines.append(f"| Moat Type | {analysis.f3_valuation.moat_type} |")
                lines.append(f"| Moat Strength | {strength} |")
            if analysis.f3_valuation.normalized_earnings is not None:
                lines.append(f"| Normalized Earnings | {_fmt_price(analysis.f3_valuation.normalized_earnings)} |")
            if analysis.f3_valuation.earning_power_multiple is not None:
                lines.append(f"| Earning Power Multiple | {analysis.f3_valuation.earning_power_multiple:.1f}x |")
            lines.append("")
        if analysis.f3_result.reasoning:
            lines.append("### Reasoning")
            lines.append("")
            lines.append(analysis.f3_result.reasoning)
            lines.append("")
    else:
        lines.append("(Filter was not executed)")
        lines.append("")
    lines.append("---")
    lines.append("")

    # Filter 4: Capital Allocation
    f4_status = _filter_status(analysis.f4_result)
    score_suffix = ""
    if analysis.f4_result and analysis.f4_result.score is not None:
        score_suffix = f" ({analysis.f4_result.score:.1f}/100)"
    lines.append(f"## Filter 4: Capital Allocation — {f4_status}{score_suffix}")
    lines.append("")
    if analysis.f4_result:
        if analysis.f4_scores:
            lines.append("| Dimension | Score |")
            lines.append("|-----------|-------|")
            lines.append(f"| Buyback Quality | {_fmt_score(analysis.f4_scores.buyback_quality)} |")
            lines.append(f"| Capital Return | {_fmt_score(analysis.f4_scores.capital_return)} |")
            lines.append(f"| Acquisition Quality | {_fmt_score(analysis.f4_scores.acquisition_quality)} |")
            lines.append(f"| Debt Management | {_fmt_score(analysis.f4_scores.debt_management)} |")
            lines.append(f"| Reinvestment Quality | {_fmt_score(analysis.f4_scores.reinvestment_quality)} |")
            lines.append("")
        if analysis.f4_result.reasoning:
            lines.append("### Reasoning")
            lines.append("")
            lines.append(analysis.f4_result.reasoning)
            lines.append("")
    else:
        lines.append("(Filter was not executed)")
        lines.append("")

    content = "\n".join(lines)
    filepath = os.path.join(output_dir, f"{ticker}.md")
    with open(filepath, "w") as f:
        f.write(content)

    return filepath
