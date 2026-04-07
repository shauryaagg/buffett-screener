"""Filter 4: Capital Allocation — 10-year capital allocation intelligence assessment."""
import json
import logging
from filters.filter_base import FilterBase, _is_rate_limit
from core.models import CompanyInfo, FilterResult, CapitalAllocationScore
from core.database import Database
from data.financial_data import FinancialDataService
from data.edgar_client import EdgarClient
from config.settings import F4_MIN_SCORE
from agents.definitions import run_capital_allocation_analysis, summarize_mda_for_capital

logger = logging.getLogger(__name__)


class CapitalAllocationFilter(FilterBase):
    filter_num = 4
    filter_name = "capital_allocation"

    def __init__(self, db: Database, financial_data: FinancialDataService, edgar: EdgarClient):
        super().__init__(db)
        self.financial_data = financial_data
        self.edgar = edgar

    async def evaluate(self, company: CompanyInfo) -> FilterResult:
        """
        Two-stage evaluation:
        Stage 1: Quantitative — compute 10-year capital allocation metrics
        Stage 2: Qualitative — summarize each year's MD&A capital allocation commentary,
                 then send all summaries + quant trends to Opus synthesis agent
        """
        # Stage 1: Get 10-year financial history
        history = self.financial_data.get_financial_history(company.ticker, years=10)
        if len(history) == 0:
            return FilterResult(
                passed=False,
                reasoning="No financial history available"
            )

        # Compute quantitative trends
        quant_analysis = self._analyze_quantitative_trends(history)

        # Stage 2: Get historical MD&A summaries
        mda_history = self.edgar.get_historical_mda(company.ticker, years=10)

        # Summarize each year's capital allocation commentary using Sonnet
        mda_summaries = []
        for filing_date, mda_text in mda_history:
            try:
                summary = await summarize_mda_for_capital(mda_text[:30000], filing_date)
                mda_summaries.append(f"--- {filing_date} ---\n{summary}")
            except Exception as e:
                if _is_rate_limit(e):
                    raise
                logger.warning(f"  {company.ticker}: error summarizing {filing_date} MD&A: {e}")
                mda_summaries.append(f"--- {filing_date} ---\n[Error extracting summary]")

        # Send to Opus synthesis agent
        mda_text = "\n\n".join(mda_summaries) if mda_summaries else "[No MD&A summaries available]"
        quant_text = self._format_quantitative_trends(quant_analysis, history)

        result = await run_capital_allocation_analysis(mda_text, quant_text)

        if result.get("error"):
            return FilterResult(
                passed=False,
                reasoning=f"Capital allocation analysis error: {result.get('error')}"
            )

        # Extract scores
        def safe_float(val, default=5.0):
            try:
                return max(0.0, min(10.0, float(val)))
            except (TypeError, ValueError):
                return default

        scores = CapitalAllocationScore(
            buyback_quality=safe_float(result.get("buyback_quality")),
            capital_return=safe_float(result.get("capital_return")),
            acquisition_quality=safe_float(result.get("acquisition_quality")),
            debt_management=safe_float(result.get("debt_management")),
            reinvestment_quality=safe_float(result.get("reinvestment_quality")),
        )

        weighted = scores.weighted_score
        passed = weighted >= F4_MIN_SCORE

        return FilterResult(
            passed=passed,
            score=weighted,
            reasoning=result.get("reasoning", ""),
            details={
                "buyback_quality": scores.buyback_quality,
                "capital_return": scores.capital_return,
                "acquisition_quality": scores.acquisition_quality,
                "debt_management": scores.debt_management,
                "reinvestment_quality": scores.reinvestment_quality,
            }
        )

    def _analyze_quantitative_trends(self, history: list) -> dict:
        """Compute quantitative capital allocation metrics across years."""
        analysis = {
            "share_count_trend": [],
            "buyback_amounts": [],
            "dividend_amounts": [],
            "acquisition_amounts": [],
            "debt_changes": [],
            "roic_trend": [],
            "cash_buildup": [],
        }

        for year_data in history:
            year = year_data.get("fiscal_year", "?")
            analysis["share_count_trend"].append({
                "year": year,
                "shares": year_data.get("shares_outstanding"),
            })
            analysis["buyback_amounts"].append({
                "year": year,
                "amount": year_data.get("buybacks"),
            })
            analysis["dividend_amounts"].append({
                "year": year,
                "amount": year_data.get("dividends"),
            })
            analysis["acquisition_amounts"].append({
                "year": year,
                "amount": year_data.get("acquisitions"),
            })
            analysis["debt_changes"].append({
                "year": year,
                "long_term_debt": year_data.get("long_term_debt"),
            })
            analysis["roic_trend"].append({
                "year": year,
                "roic": year_data.get("roic"),
            })
            analysis["cash_buildup"].append({
                "year": year,
                "cash": year_data.get("cash"),
                "net_income": year_data.get("net_income"),
            })

        # Compute share count change
        shares = [s["shares"] for s in analysis["share_count_trend"] if s["shares"]]
        if len(shares) >= 2:
            oldest = shares[-1]
            newest = shares[0]
            if oldest and oldest > 0:
                analysis["share_count_change_pct"] = (newest - oldest) / oldest * 100

        return analysis

    def _format_quantitative_trends(self, analysis: dict, history: list) -> str:
        """Format quantitative data as text for the agent."""
        lines = ["=== 10-YEAR QUANTITATIVE CAPITAL ALLOCATION DATA ===\n"]

        # Share count trend
        share_change = analysis.get("share_count_change_pct")
        if share_change is not None:
            direction = "INCREASED (dilution)" if share_change > 0 else "DECREASED (accretive)"
            lines.append(f"Share count over period: {direction} by {abs(share_change):.1f}%\n")

        lines.append("Year-by-Year Data:")
        for year_data in history:
            year = year_data.get("fiscal_year", "?")
            lines.append(f"\n  {year}:")

            for key in ["shares_outstanding", "buybacks", "dividends", "acquisitions",
                        "long_term_debt", "cash", "net_income", "operating_cash_flow",
                        "owner_earnings", "stock_comp"]:
                val = year_data.get(key)
                if val is not None:
                    if key == "shares_outstanding":
                        lines.append(f"    {key}: {val:,.0f}")
                    else:
                        lines.append(f"    {key}: ${val:,.0f}")

            roic = year_data.get("roic")
            if roic is not None:
                lines.append(f"    roic: {roic:.2%}")

            roe = year_data.get("roe")
            if roe is not None:
                lines.append(f"    roe: {roe:.2%}")

        return "\n".join(lines)
