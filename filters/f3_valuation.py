"""Filter 3: Valuation — earning power value + moat assessment + margin of safety."""
import json
import logging
from filters.filter_base import FilterBase
from core.models import CompanyInfo, FilterResult
from core.database import Database
from data.financial_data import FinancialDataService
from data.edgar_client import EdgarClient
from data.market_data import MarketDataService
from config.settings import F3_MARGIN_OF_SAFETY
from agents.definitions import run_valuation_analysis

logger = logging.getLogger(__name__)


class ValuationFilter(FilterBase):
    filter_num = 3
    filter_name = "valuation"

    def __init__(self, db: Database, financial_data: FinancialDataService,
                 edgar: EdgarClient, market_data: MarketDataService):
        super().__init__(db)
        self.financial_data = financial_data
        self.edgar = edgar
        self.market_data = market_data

    async def evaluate(self, company: CompanyInfo) -> FilterResult:
        """
        Two-stage valuation:
        1. Prepare financial context (pure Python — 10yr financials, derived metrics)
        2. Run Opus valuation agent with financial summary + business description
        3. Check margin of safety >= F3_MARGIN_OF_SAFETY (50%)
        """
        # Step 1: Get financial summary
        fin_summary = self.financial_data.get_financial_summary(company.ticker)
        if fin_summary.get("error"):
            return FilterResult(
                passed=False,
                reasoning=f"No financial data: {fin_summary['error']}"
            )

        # Get current price
        price = company.price
        if not price and self.market_data:
            quote = self.market_data.get_single_quote(company.ticker)
            if quote:
                price = quote["price"]

        if not price or price <= 0:
            return FilterResult(
                passed=False,
                reasoning="Cannot determine current stock price"
            )

        # Get business description for context
        sections = self.edgar.get_tenk_sections(company.ticker)
        business_desc = sections.get("item_1", "")[:20000]
        mda_text = sections.get("item_7", "")[:20000]
        business_context = f"Business Description:\n{business_desc}\n\nMD&A Highlights:\n{mda_text}"

        # Format financial summary as text for the agent
        fin_text = self._format_financial_summary(fin_summary, price)

        # Step 2: Run Opus valuation agent
        val_result = await run_valuation_analysis(fin_text, business_context)

        if val_result.get("error"):
            return FilterResult(
                passed=False,
                reasoning=f"Valuation analysis error: {val_result.get('error')}"
            )

        # Step 3: Extract results and check margin of safety
        def safe_float(val, default=0.0):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default

        intrinsic_value = safe_float(val_result.get("intrinsic_value_per_share"), 0.0)
        margin_of_safety = safe_float(val_result.get("margin_of_safety"), 0.0)
        moat_type = val_result.get("moat_type", "unknown")
        moat_strength = safe_float(val_result.get("moat_strength"), 0.0)

        # Recalculate margin of safety to be safe
        if intrinsic_value > 0:
            margin_of_safety = (intrinsic_value - price) / intrinsic_value

        passed = margin_of_safety >= F3_MARGIN_OF_SAFETY

        normalized_earnings = fin_summary.get("normalized_owner_earnings")
        earning_power_multiple = None
        if normalized_earnings and normalized_earnings > 0 and price:
            shares = self._estimate_shares(fin_summary)
            if shares and shares > 0:
                eps = normalized_earnings / shares
                if eps > 0:
                    earning_power_multiple = price / eps

        reasoning = val_result.get("reasoning", "")

        return FilterResult(
            passed=passed,
            score=margin_of_safety * 100,
            reasoning=reasoning,
            details={
                "normalized_earnings": normalized_earnings,
                "moat_type": moat_type,
                "moat_strength": moat_strength,
                "earning_power_multiple": earning_power_multiple,
                "intrinsic_value": intrinsic_value,
                "current_price": price,
                "margin_of_safety": margin_of_safety,
            }
        )

    def _format_financial_summary(self, summary: dict, current_price: float) -> str:
        """Format financial data as readable text for the valuation agent."""
        lines = [f"Ticker: {summary['ticker']}"]
        lines.append(f"Current Stock Price: ${current_price:.2f}")
        lines.append(f"Years of data: {summary['years_of_data']}")

        if summary.get("normalized_owner_earnings"):
            lines.append(f"Normalized Owner Earnings (5yr trimmed avg): ${summary['normalized_owner_earnings']:,.0f}")

        lines.append("\n--- Annual Financial Data (most recent first) ---\n")

        for i, year_data in enumerate(summary.get("history", [])):
            year = year_data.get("fiscal_year", f"Year {i}")
            lines.append(f"\n=== {year} ===")

            for key in ["revenue", "net_income", "operating_income", "owner_earnings",
                        "operating_cash_flow", "capex", "depreciation",
                        "total_assets", "total_equity", "total_liabilities",
                        "cash", "long_term_debt", "buybacks", "dividends",
                        "shares_outstanding", "stock_comp"]:
                val = year_data.get(key)
                if val is not None:
                    if key == "shares_outstanding":
                        lines.append(f"  {key}: {val:,.0f}")
                    else:
                        lines.append(f"  {key}: ${val:,.0f}")

            for key in ["roe", "roa", "roic", "net_margin", "operating_margin", "capital_intensity"]:
                val = year_data.get(key)
                if val is not None:
                    lines.append(f"  {key}: {val:.2%}")

        return "\n".join(lines)

    def _estimate_shares(self, summary: dict) -> float | None:
        """Estimate shares outstanding from the most recent year."""
        for year_data in summary.get("history", []):
            shares = year_data.get("shares_outstanding")
            if shares and shares > 0:
                return shares
        return None
