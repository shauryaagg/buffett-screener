"""Tests for filter logic (F1-F4).

All external dependencies (EdgarClient, MarketDataService, FinancialDataService,
agent calls) are mocked so tests run without network or API keys.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from core.models import CompanyInfo, FilterResult
from core.database import Database
from filters.f1_business_type import BusinessTypeFilter
from filters.f2_management_quality import ManagementQualityFilter
from filters.f3_valuation import ValuationFilter
from filters.f4_capital_allocation import CapitalAllocationFilter


# ===========================================================================
# Helpers / Fixtures
# ===========================================================================

@pytest.fixture
def memory_db(tmp_path):
    db_file = str(tmp_path / "test.db")
    db = Database(db_path=db_file)
    db.init_db()
    return db


def _make_company(**overrides):
    defaults = dict(
        ticker="ACME",
        name="Acme Corp",
        sic=3559,
        industry="Industrial Machinery",
        market_cap=500_000_000,
        price=25.0,
        exchange="NASDAQ",
    )
    defaults.update(overrides)
    return CompanyInfo(**defaults)


# ===========================================================================
# Filter 1: BusinessTypeFilter
# ===========================================================================

class TestF1BusinessType:

    def _make_filter(self, memory_db, edgar=None, market_data=None):
        edgar = edgar or MagicMock()
        market_data = market_data or MagicMock()
        return BusinessTypeFilter(memory_db, edgar, market_data)

    @pytest.mark.asyncio
    async def test_excluded_sic_fails(self, memory_db):
        """SIC 1040 (gold mining) is in SIC_EXCLUSIONS."""
        edgar = MagicMock()
        filt = self._make_filter(memory_db, edgar=edgar)
        company = _make_company(sic=1040, name="GoldMiner")

        result = await filt.evaluate(company)
        assert result.passed is False
        assert "excluded" in result.reasoning.lower() or "SIC 1040" in result.reasoning

    @pytest.mark.asyncio
    async def test_included_sic_with_tenk_passes(self, memory_db):
        """SIC 3559 (manufacturing) is in SIC_INCLUSIONS + has a 10-K => pass."""
        edgar = MagicMock()
        edgar.has_tenk.return_value = True
        filt = self._make_filter(memory_db, edgar=edgar)
        company = _make_company(sic=3559)

        result = await filt.evaluate(company)
        assert result.passed is True
        assert "included" in result.reasoning.lower() or "10-K" in result.reasoning

    @pytest.mark.asyncio
    async def test_market_cap_below_range_fails(self, memory_db):
        edgar = MagicMock()
        filt = self._make_filter(memory_db, edgar=edgar)
        company = _make_company(market_cap=1_000_000)  # $1M, below $5M min

        result = await filt.evaluate(company)
        assert result.passed is False
        assert "market cap" in result.reasoning.lower() or "outside" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_market_cap_above_range_fails(self, memory_db):
        edgar = MagicMock()
        filt = self._make_filter(memory_db, edgar=edgar)
        company = _make_company(market_cap=10_000_000_000)  # $10B, above $5B max

        result = await filt.evaluate(company)
        assert result.passed is False
        assert "outside" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_no_tenk_fails(self, memory_db):
        """Even with a valid SIC, no 10-K filing => fail."""
        edgar = MagicMock()
        edgar.has_tenk.return_value = False
        filt = self._make_filter(memory_db, edgar=edgar)
        company = _make_company(sic=3559)

        result = await filt.evaluate(company)
        assert result.passed is False
        assert "10-K" in result.reasoning

    @pytest.mark.asyncio
    async def test_no_sic_on_company_falls_back_to_edgar(self, memory_db):
        """When company.sic is None, the filter calls edgar.get_company_sic."""
        edgar = MagicMock()
        edgar.get_company_sic.return_value = 3559  # included SIC
        edgar.has_tenk.return_value = True
        filt = self._make_filter(memory_db, edgar=edgar)
        company = _make_company(sic=None)

        result = await filt.evaluate(company)
        edgar.get_company_sic.assert_called_once_with("ACME")
        assert result.passed is True

    def test_build_universe_creates_company_info(self, memory_db):
        market_data = MagicMock()
        market_data.fetch_all_prices.return_value = {
            "AAPL": {"name": "Apple", "market_cap": 1e9, "price": 150, "exchange": "NASDAQ"},
            "MSFT": {"name": "Microsoft", "market_cap": 2e9, "price": 300, "exchange": "NASDAQ"},
        }
        filt = self._make_filter(memory_db, market_data=market_data)

        universe = filt.build_universe()
        assert len(universe) == 2
        assert all(isinstance(c, CompanyInfo) for c in universe)
        tickers = {c.ticker for c in universe}
        assert tickers == {"AAPL", "MSFT"}


# ===========================================================================
# Filter 2: ManagementQualityFilter
# ===========================================================================

class TestF2ManagementQuality:

    def _make_filter(self, memory_db, edgar=None):
        edgar = edgar or MagicMock()
        return ManagementQualityFilter(memory_db, edgar)

    @pytest.mark.asyncio
    async def test_missing_tenk_sections_fails(self, memory_db):
        edgar = MagicMock()
        edgar.get_tenk_sections.return_value = {}
        filt = self._make_filter(memory_db, edgar=edgar)
        company = _make_company()

        result = await filt.evaluate(company)
        assert result.passed is False
        assert "10-K" in result.reasoning.lower() or "extract" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_missing_both_item1_and_item7_fails(self, memory_db):
        edgar = MagicMock()
        edgar.get_tenk_sections.return_value = {"item_1a": "risk factors here"}
        filt = self._make_filter(memory_db, edgar=edgar)
        company = _make_company()

        result = await filt.evaluate(company)
        assert result.passed is False
        assert "Item 1" in result.reasoning or "Item 7" in result.reasoning

    @pytest.mark.asyncio
    @patch("filters.f2_management_quality.analyze_business_description", new_callable=AsyncMock)
    async def test_early_exit_low_business_clarity(self, mock_biz, memory_db):
        """business_clarity < 3 triggers early exit before the expensive MD&A call."""
        edgar = MagicMock()
        edgar.get_tenk_sections.return_value = {
            "item_1": "biz desc",
            "item_1a": "risks",
            "item_7": "mda text",
        }
        mock_biz.return_value = {
            "business_clarity": 2.0,
            "moat_articulation": 3.0,
            "honest_self_assessment": 2.0,
            "reasoning": "very vague",
        }

        filt = self._make_filter(memory_db, edgar=edgar)
        result = await filt.evaluate(_make_company())

        assert result.passed is False
        assert "early exit" in result.reasoning.lower() or "business clarity" in result.reasoning.lower()
        assert result.score is not None
        assert result.score == pytest.approx(20.0)  # 2.0 * 10

    @pytest.mark.asyncio
    @patch("filters.f2_management_quality.analyze_mda", new_callable=AsyncMock)
    @patch("filters.f2_management_quality.analyze_risk_factors", new_callable=AsyncMock)
    @patch("filters.f2_management_quality.analyze_business_description", new_callable=AsyncMock)
    async def test_happy_path_all_agents_score(self, mock_biz, mock_risk, mock_mda, memory_db):
        """Full happy path: all 3 agents return valid scores, weighted score computed."""
        edgar = MagicMock()
        edgar.get_tenk_sections.return_value = {
            "item_1": "clear business description",
            "item_1a": "honest risk factors",
            "item_7": "transparent management discussion",
        }
        mock_biz.return_value = {
            "business_clarity": 8.0,
            "moat_articulation": 7.0,
            "honest_self_assessment": 6.0,
            "reasoning": "Clear business model",
        }
        mock_risk.return_value = {
            "risk_honesty": 7.0,
            "specificity": 6.0,
            "quantification": 5.0,
            "reasoning": "Decent risk disclosure",
        }
        mock_mda.return_value = {
            "kpi_quality": 8.0,
            "transparency": 7.0,
            "explanation_quality": 7.0,
            "capital_allocation_discussion": 6.0,
            "forward_looking_honesty": 7.0,
            "reasoning": "Good management discussion",
        }

        filt = self._make_filter(memory_db, edgar=edgar)
        result = await filt.evaluate(_make_company())

        # Business avg = (8+7+6)/3 = 7.0
        # Risk avg = (7+6+5)/3 = 6.0
        # MDA: kpi = (8+6)/2 = 7.0, transparency = 7.0, tone = (7+7)/2 = 7.0
        # MD&A avg = (7+7+7)/3 = 7.0
        # Weighted = (7.0*0.50 + 7.0*0.25 + 6.0*0.25) * 10 = (3.5 + 1.75 + 1.5) * 10 = 67.5
        assert result.score is not None
        assert result.score == pytest.approx(67.5, abs=0.5)
        assert result.passed is True  # 67.5 >= 65

    @pytest.mark.asyncio
    @patch("filters.f2_management_quality.analyze_mda", new_callable=AsyncMock)
    @patch("filters.f2_management_quality.analyze_risk_factors", new_callable=AsyncMock)
    @patch("filters.f2_management_quality.analyze_business_description", new_callable=AsyncMock)
    async def test_happy_path_fails_below_threshold(self, mock_biz, mock_risk, mock_mda, memory_db):
        """Scores below threshold → fails."""
        edgar = MagicMock()
        edgar.get_tenk_sections.return_value = {
            "item_1": "vague description",
            "item_1a": "boilerplate risks",
            "item_7": "generic discussion",
        }
        mock_biz.return_value = {
            "business_clarity": 5.0,
            "moat_articulation": 4.0,
            "honest_self_assessment": 3.0,
            "reasoning": "Vague",
        }
        mock_risk.return_value = {
            "risk_honesty": 4.0,
            "specificity": 3.0,
            "quantification": 2.0,
            "reasoning": "Boilerplate",
        }
        mock_mda.return_value = {
            "kpi_quality": 5.0,
            "transparency": 4.0,
            "explanation_quality": 4.0,
            "capital_allocation_discussion": 3.0,
            "forward_looking_honesty": 3.0,
            "reasoning": "Generic",
        }

        filt = self._make_filter(memory_db, edgar=edgar)
        result = await filt.evaluate(_make_company())

        # Business avg = (5+4+3)/3 = 4.0
        # Risk avg = (4+3+2)/3 = 3.0
        # MDA: kpi = (5+3)/2 = 4.0, transparency = 4.0, tone = (4+3)/2 = 3.5
        # MD&A avg = (4+4+3.5)/3 ≈ 3.833
        # Weighted = (3.833*0.50 + 4.0*0.25 + 3.0*0.25) * 10 = (1.917 + 1.0 + 0.75) * 10 = 36.67
        assert result.score is not None
        assert result.score < 65  # Below threshold
        assert result.passed is False


# ===========================================================================
# Filter 3: ValuationFilter
# ===========================================================================

class TestF3Valuation:

    def _make_filter(self, memory_db, financial_data=None, edgar=None, market_data=None):
        financial_data = financial_data or MagicMock()
        edgar = edgar or MagicMock()
        market_data = market_data or MagicMock()
        return ValuationFilter(memory_db, financial_data, edgar, market_data)

    @pytest.mark.asyncio
    async def test_missing_financial_data_fails(self, memory_db):
        fin = MagicMock()
        fin.get_financial_summary.return_value = {"error": "No data"}
        filt = self._make_filter(memory_db, financial_data=fin)
        company = _make_company()

        result = await filt.evaluate(company)
        assert result.passed is False
        assert "financial data" in result.reasoning.lower() or "No data" in result.reasoning

    @pytest.mark.asyncio
    async def test_no_price_and_no_market_data_fails_gracefully(self, memory_db):
        """When company.price is None and market_data returns nothing, should not crash."""
        fin = MagicMock()
        fin.get_financial_summary.return_value = {
            "ticker": "ACME",
            "years_of_data": 5,
            "history": [{"fiscal_year": 2024}],
            "normalized_owner_earnings": 1000000,
        }
        market_data = MagicMock()
        market_data.get_single_quote.return_value = None
        edgar = MagicMock()
        edgar.get_tenk_sections.return_value = {"item_1": "desc", "item_7": "mda"}

        filt = self._make_filter(memory_db, financial_data=fin, edgar=edgar, market_data=market_data)
        company = _make_company(price=None)

        result = await filt.evaluate(company)
        assert result.passed is False
        assert "price" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_no_price_zero_price_fails(self, memory_db):
        fin = MagicMock()
        fin.get_financial_summary.return_value = {
            "ticker": "ACME",
            "years_of_data": 5,
            "history": [],
            "normalized_owner_earnings": 1000000,
        }
        market_data = MagicMock()
        market_data.get_single_quote.return_value = {"price": 0}
        edgar = MagicMock()
        edgar.get_tenk_sections.return_value = {}

        filt = self._make_filter(memory_db, financial_data=fin, edgar=edgar, market_data=market_data)
        company = _make_company(price=None)

        result = await filt.evaluate(company)
        assert result.passed is False
        assert "price" in result.reasoning.lower()


# ===========================================================================
# Filter 4: CapitalAllocationFilter
# ===========================================================================

class TestF4CapitalAllocation:

    def _make_filter(self, memory_db, financial_data=None, edgar=None):
        financial_data = financial_data or MagicMock()
        edgar = edgar or MagicMock()
        return CapitalAllocationFilter(memory_db, financial_data, edgar)

    @pytest.mark.asyncio
    async def test_insufficient_history_fails(self, memory_db):
        """Fewer than 3 years of history => fail."""
        fin = MagicMock()
        fin.get_financial_history.return_value = [
            {"fiscal_year": 2024},
            {"fiscal_year": 2023},
        ]
        filt = self._make_filter(memory_db, financial_data=fin)
        company = _make_company()

        result = await filt.evaluate(company)
        assert result.passed is False
        assert "insufficient" in result.reasoning.lower() or "2 years" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_exactly_three_years_proceeds(self, memory_db):
        """3 years is the minimum — should not fail on history length."""
        fin = MagicMock()
        fin.get_financial_history.return_value = [
            {"fiscal_year": 2024, "shares_outstanding": 1000},
            {"fiscal_year": 2023, "shares_outstanding": 1100},
            {"fiscal_year": 2022, "shares_outstanding": 1200},
        ]
        edgar = MagicMock()
        edgar.get_historical_mda.return_value = []

        with patch(
            "filters.f4_capital_allocation.run_capital_allocation_analysis",
            new_callable=AsyncMock,
        ) as mock_agent:
            mock_agent.return_value = {
                "buyback_quality": 7,
                "capital_return": 7,
                "acquisition_quality": 7,
                "debt_management": 7,
                "reinvestment_quality": 7,
                "reasoning": "Good allocator",
            }
            filt = self._make_filter(memory_db, financial_data=fin, edgar=edgar)
            result = await filt.evaluate(_make_company())

        # With all 7s: weighted = 7/5*5 * 10 = 70, above F4_MIN_SCORE (60)
        assert result.passed is True
        assert result.score == pytest.approx(70.0)
