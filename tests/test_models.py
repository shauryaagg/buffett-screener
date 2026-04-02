"""Tests for core/models.py — Pydantic model validation and computed properties."""
import pytest
from datetime import datetime
from pydantic import ValidationError

from core.models import (
    CompanyInfo,
    FilterResult,
    ManagementQualityScore,
    CapitalAllocationScore,
    ValuationResult,
    PipelineState,
    PipelineStatus,
    FullAnalysis,
)


# ---------------------------------------------------------------------------
# ManagementQualityScore.weighted_score
# ---------------------------------------------------------------------------

class TestManagementQualityScore:
    """MD&A 50%, Business 25%, Risk 25%. Scale to 0-100."""

    def test_weighted_score_concrete_values(self):
        """Verify weighting with hand-calculated numbers.

        business_clarity=8, risk_honesty=6,
        mda_transparency=7, kpi_quality=9, tone_authenticity=5

        mda_avg = (7 + 9 + 5) / 3 = 7.0
        weighted = (7.0 * 0.50 + 8 * 0.25 + 6 * 0.25) * 10
                 = (3.5 + 2.0 + 1.5) * 10 = 70.0
        """
        score = ManagementQualityScore(
            business_clarity=8,
            risk_honesty=6,
            mda_transparency=7,
            kpi_quality=9,
            tone_authenticity=5,
        )
        assert score.weighted_score == pytest.approx(70.0)

    def test_weighted_score_all_tens(self):
        score = ManagementQualityScore(
            business_clarity=10,
            risk_honesty=10,
            mda_transparency=10,
            kpi_quality=10,
            tone_authenticity=10,
        )
        # mda_avg = 10, weighted = (10*0.5 + 10*0.25 + 10*0.25)*10 = 100
        assert score.weighted_score == pytest.approx(100.0)

    def test_weighted_score_all_zeros(self):
        score = ManagementQualityScore(
            business_clarity=0,
            risk_honesty=0,
            mda_transparency=0,
            kpi_quality=0,
            tone_authenticity=0,
        )
        assert score.weighted_score == pytest.approx(0.0)

    def test_weighted_score_mda_dominant(self):
        """When only MD&A fields are nonzero the score is 50% of max."""
        score = ManagementQualityScore(
            business_clarity=0,
            risk_honesty=0,
            mda_transparency=10,
            kpi_quality=10,
            tone_authenticity=10,
        )
        # mda_avg = 10, weighted = (10*0.5 + 0 + 0)*10 = 50
        assert score.weighted_score == pytest.approx(50.0)

    def test_rejects_value_below_zero(self):
        with pytest.raises(ValidationError):
            ManagementQualityScore(
                business_clarity=-1,
                risk_honesty=5,
                mda_transparency=5,
                kpi_quality=5,
                tone_authenticity=5,
            )

    def test_rejects_value_above_ten(self):
        with pytest.raises(ValidationError):
            ManagementQualityScore(
                business_clarity=5,
                risk_honesty=5,
                mda_transparency=11,
                kpi_quality=5,
                tone_authenticity=5,
            )


# ---------------------------------------------------------------------------
# CapitalAllocationScore.weighted_score
# ---------------------------------------------------------------------------

class TestCapitalAllocationScore:
    """Equal weighting across 5 dimensions, scale to 0-100."""

    def test_weighted_score_concrete_values(self):
        """
        Each field equally weighted.
        total = (6 + 8 + 4 + 7 + 5) = 30
        weighted = 30 / 5 * 10 = 60.0
        """
        score = CapitalAllocationScore(
            buyback_quality=6,
            capital_return=8,
            acquisition_quality=4,
            debt_management=7,
            reinvestment_quality=5,
        )
        assert score.weighted_score == pytest.approx(60.0)

    def test_weighted_score_all_tens(self):
        score = CapitalAllocationScore(
            buyback_quality=10,
            capital_return=10,
            acquisition_quality=10,
            debt_management=10,
            reinvestment_quality=10,
        )
        assert score.weighted_score == pytest.approx(100.0)

    def test_weighted_score_all_zeros(self):
        score = CapitalAllocationScore(
            buyback_quality=0,
            capital_return=0,
            acquisition_quality=0,
            debt_management=0,
            reinvestment_quality=0,
        )
        assert score.weighted_score == pytest.approx(0.0)

    def test_rejects_value_outside_range(self):
        with pytest.raises(ValidationError):
            CapitalAllocationScore(
                buyback_quality=10,
                capital_return=10,
                acquisition_quality=10,
                debt_management=10,
                reinvestment_quality=10.1,
            )


# ---------------------------------------------------------------------------
# CompanyInfo
# ---------------------------------------------------------------------------

class TestCompanyInfo:

    def test_minimal_creation(self):
        c = CompanyInfo(ticker="AAPL", name="Apple Inc.")
        assert c.ticker == "AAPL"
        assert c.name == "Apple Inc."
        assert c.sic is None
        assert c.market_cap is None
        assert c.price is None

    def test_full_creation(self):
        c = CompanyInfo(
            ticker="MSFT",
            name="Microsoft Corp",
            sic=7372,
            industry="Software",
            market_cap=3_000_000_000_000.0,
            price=400.0,
            exchange="NASDAQ",
        )
        assert c.sic == 7372
        assert c.market_cap == 3_000_000_000_000.0
        assert c.exchange == "NASDAQ"


# ---------------------------------------------------------------------------
# FilterResult
# ---------------------------------------------------------------------------

class TestFilterResult:

    def test_defaults(self):
        r = FilterResult(passed=True)
        assert r.passed is True
        assert r.score is None
        assert r.reasoning == ""
        assert r.details == {}

    def test_with_all_fields(self):
        r = FilterResult(
            passed=False,
            score=42.5,
            reasoning="below threshold",
            details={"business_clarity": 3.0},
        )
        assert r.passed is False
        assert r.score == 42.5
        assert "below" in r.reasoning
        assert r.details["business_clarity"] == 3.0


# ---------------------------------------------------------------------------
# PipelineStatus enum
# ---------------------------------------------------------------------------

class TestPipelineStatus:

    def test_values(self):
        assert PipelineStatus.RUNNING.value == "running"
        assert PipelineStatus.PAUSED.value == "paused"
        assert PipelineStatus.COMPLETED.value == "completed"
        assert PipelineStatus.FAILED.value == "failed"

    def test_from_string(self):
        assert PipelineStatus("running") is PipelineStatus.RUNNING
        assert PipelineStatus("completed") is PipelineStatus.COMPLETED

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            PipelineStatus("invalid")


# ---------------------------------------------------------------------------
# PipelineState
# ---------------------------------------------------------------------------

class TestPipelineState:

    def test_defaults(self):
        s = PipelineState(run_id="abc123")
        assert s.current_filter == 1
        assert s.current_ticker_idx == 0
        assert s.status == PipelineStatus.RUNNING
        assert s.started_at is None

    def test_with_dates(self):
        now = datetime.now()
        s = PipelineState(
            run_id="r1",
            started_at=now,
            status=PipelineStatus.COMPLETED,
            completed_at=now,
            ticker_limit=50,
        )
        assert s.started_at == now
        assert s.ticker_limit == 50


# ---------------------------------------------------------------------------
# FullAnalysis
# ---------------------------------------------------------------------------

class TestFullAnalysis:

    def test_creation_minimal(self):
        c = CompanyInfo(ticker="X", name="X Corp")
        a = FullAnalysis(company=c)
        assert a.company.ticker == "X"
        assert a.final_passed is False
        assert a.f1_result is None
        assert a.f2_scores is None

    def test_creation_with_results(self):
        c = CompanyInfo(ticker="Y", name="Y Inc")
        a = FullAnalysis(
            company=c,
            f1_result=FilterResult(passed=True, reasoning="ok"),
            final_passed=True,
            analyzed_at=datetime(2025, 1, 1),
        )
        assert a.f1_result.passed is True
        assert a.final_passed is True
        assert a.analyzed_at.year == 2025


# ---------------------------------------------------------------------------
# ValuationResult
# ---------------------------------------------------------------------------

class TestValuationResult:

    def test_defaults(self):
        v = ValuationResult()
        assert v.normalized_earnings is None
        assert v.reasoning == ""

    def test_moat_strength_validation(self):
        v = ValuationResult(moat_strength=7.5)
        assert v.moat_strength == 7.5

        with pytest.raises(ValidationError):
            ValuationResult(moat_strength=11)

        with pytest.raises(ValidationError):
            ValuationResult(moat_strength=-0.1)
