"""Filter 1: Business Type + Market Cap screening."""
import logging
from typing import List
from filters.filter_base import FilterBase
from core.models import CompanyInfo, FilterResult
from core.database import Database
from data.edgar_client import EdgarClient
from data.market_data import MarketDataService
from config.settings import SIC_EXCLUSIONS, SIC_INCLUSIONS, MARKET_CAP_MIN, MARKET_CAP_MAX
from agents.definitions import classify_business_type

logger = logging.getLogger(__name__)


class BusinessTypeFilter(FilterBase):
    filter_num = 1
    filter_name = "business_type"

    def __init__(self, db: Database, edgar: EdgarClient, market_data: MarketDataService):
        super().__init__(db)
        self.edgar = edgar
        self.market_data = market_data

    async def evaluate(self, company: CompanyInfo) -> FilterResult:
        """
        Evaluate a single company:
        1. Check market cap is in $5M-$5B range
        2. Check SIC code against exclusions/inclusions
        3. For ambiguous SIC codes, use Claude Haiku to classify
        4. Verify at least one 10-K filing exists
        """
        reasons = []

        # 1. Market cap check
        if company.market_cap is not None:
            if company.market_cap < MARKET_CAP_MIN or company.market_cap > MARKET_CAP_MAX:
                return FilterResult(
                    passed=False,
                    reasoning=f"Market cap ${company.market_cap/1e6:.1f}M outside ${MARKET_CAP_MIN/1e6:.0f}M-${MARKET_CAP_MAX/1e9:.0f}B range"
                )

        # 2. SIC code check
        sic = company.sic
        if sic is None:
            sic = self.edgar.get_company_sic(company.ticker)

        if sic is not None:
            if sic in SIC_EXCLUSIONS:
                return FilterResult(
                    passed=False,
                    reasoning=f"SIC {sic} is in excluded commodity/non-product category"
                )

            if sic in SIC_INCLUSIONS:
                reasons.append(f"SIC {sic} is an included product business category")
            else:
                # Ambiguous SIC — use agent classification
                try:
                    classification = await classify_business_type(
                        company.name, sic, company.industry or ""
                    )
                    if classification.get("error"):
                        reasons.append(f"SIC {sic} ambiguous, classification failed — allowing through")
                    elif not classification.get("is_product_business", True):
                        confidence = classification.get("confidence", 0)
                        if confidence > 0.7:
                            return FilterResult(
                                passed=False,
                                reasoning=f"Classified as non-product business (confidence: {confidence:.0%}): {classification.get('reasoning', '')}"
                            )
                        else:
                            reasons.append(f"SIC {sic} ambiguous, low-confidence non-product ({confidence:.0%}) — allowing through")
                    else:
                        reasons.append(f"Classified as product business: {classification.get('reasoning', '')}")
                except Exception as e:
                    logger.warning(f"  {company.ticker}: classification error: {e} — allowing through")
                    reasons.append(f"SIC {sic} ambiguous, classification error — allowing through")
        else:
            reasons.append("No SIC code found — allowing through for manual review")

        # 3. Verify 10-K filing exists
        has_filing = self.edgar.has_tenk(company.ticker)
        if not has_filing:
            return FilterResult(
                passed=False,
                reasoning="No 10-K filing found on EDGAR"
            )
        reasons.append("Has 10-K filing on EDGAR")

        return FilterResult(
            passed=True,
            reasoning="; ".join(reasons)
        )

    def build_universe(self) -> List[CompanyInfo]:
        """
        Build the initial universe of companies from FMP market data.
        Returns list of CompanyInfo objects in the $5M-$5B market cap range.
        """
        all_prices = self.market_data.fetch_all_prices()
        companies = []
        for ticker, data in all_prices.items():
            companies.append(CompanyInfo(
                ticker=ticker,
                name=data.get("name", ticker),
                market_cap=data.get("market_cap"),
                price=data.get("price"),
                exchange=data.get("exchange"),
            ))
        logger.info(f"Built universe of {len(companies)} companies in market cap range")
        return companies
