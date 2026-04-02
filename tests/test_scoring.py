"""Tests for scoring edge cases — ManagementQualityScore and CapitalAllocationScore."""
import pytest
from pydantic import ValidationError
from core.models import ManagementQualityScore, CapitalAllocationScore


# ===========================================================================
# ManagementQualityScore
# ===========================================================================

class TestManagementQualityScoringEdgeCases:

    def test_all_zeros(self):
        s = ManagementQualityScore(
            business_clarity=0, risk_honesty=0,
            mda_transparency=0, kpi_quality=0, tone_authenticity=0,
        )
        assert s.weighted_score == pytest.approx(0.0)

    def test_all_tens(self):
        s = ManagementQualityScore(
            business_clarity=10, risk_honesty=10,
            mda_transparency=10, kpi_quality=10, tone_authenticity=10,
        )
        assert s.weighted_score == pytest.approx(100.0)

    def test_mixed_scores(self):
        """
        business_clarity=4, risk_honesty=6,
        mda_transparency=8, kpi_quality=2, tone_authenticity=10

        mda_avg = (8 + 2 + 10) / 3 = 6.6667
        weighted = (6.6667 * 0.50 + 4 * 0.25 + 6 * 0.25) * 10
                 = (3.3333 + 1.0 + 1.5) * 10
                 = 58.333...
        """
        s = ManagementQualityScore(
            business_clarity=4, risk_honesty=6,
            mda_transparency=8, kpi_quality=2, tone_authenticity=10,
        )
        expected = ((8 + 2 + 10) / 3 * 0.5 + 4 * 0.25 + 6 * 0.25) * 10
        assert s.weighted_score == pytest.approx(expected)

    @pytest.mark.parametrize("field", [
        "business_clarity", "risk_honesty",
        "mda_transparency", "kpi_quality", "tone_authenticity",
    ])
    def test_rejects_below_zero(self, field):
        kwargs = {
            "business_clarity": 5, "risk_honesty": 5,
            "mda_transparency": 5, "kpi_quality": 5, "tone_authenticity": 5,
        }
        kwargs[field] = -0.01
        with pytest.raises(ValidationError):
            ManagementQualityScore(**kwargs)

    @pytest.mark.parametrize("field", [
        "business_clarity", "risk_honesty",
        "mda_transparency", "kpi_quality", "tone_authenticity",
    ])
    def test_rejects_above_ten(self, field):
        kwargs = {
            "business_clarity": 5, "risk_honesty": 5,
            "mda_transparency": 5, "kpi_quality": 5, "tone_authenticity": 5,
        }
        kwargs[field] = 10.01
        with pytest.raises(ValidationError):
            ManagementQualityScore(**kwargs)

    def test_boundary_zero_accepted(self):
        s = ManagementQualityScore(
            business_clarity=0, risk_honesty=0,
            mda_transparency=0, kpi_quality=0, tone_authenticity=0,
        )
        assert s.business_clarity == 0

    def test_boundary_ten_accepted(self):
        s = ManagementQualityScore(
            business_clarity=10, risk_honesty=10,
            mda_transparency=10, kpi_quality=10, tone_authenticity=10,
        )
        assert s.tone_authenticity == 10

    def test_floats_within_range(self):
        s = ManagementQualityScore(
            business_clarity=5.5, risk_honesty=3.3,
            mda_transparency=7.7, kpi_quality=1.1, tone_authenticity=9.9,
        )
        mda_avg = (7.7 + 1.1 + 9.9) / 3
        expected = (mda_avg * 0.5 + 5.5 * 0.25 + 3.3 * 0.25) * 10
        assert s.weighted_score == pytest.approx(expected)


# ===========================================================================
# CapitalAllocationScore
# ===========================================================================

class TestCapitalAllocationScoringEdgeCases:

    def test_all_zeros(self):
        s = CapitalAllocationScore(
            buyback_quality=0, capital_return=0,
            acquisition_quality=0, debt_management=0, reinvestment_quality=0,
        )
        assert s.weighted_score == pytest.approx(0.0)

    def test_all_tens(self):
        s = CapitalAllocationScore(
            buyback_quality=10, capital_return=10,
            acquisition_quality=10, debt_management=10, reinvestment_quality=10,
        )
        assert s.weighted_score == pytest.approx(100.0)

    def test_mixed_equal_weight(self):
        """
        (2 + 4 + 6 + 8 + 10) = 30
        weighted = 30 / 5 * 10 = 60.0
        """
        s = CapitalAllocationScore(
            buyback_quality=2, capital_return=4,
            acquisition_quality=6, debt_management=8, reinvestment_quality=10,
        )
        assert s.weighted_score == pytest.approx(60.0)

    @pytest.mark.parametrize("field", [
        "buyback_quality", "capital_return",
        "acquisition_quality", "debt_management", "reinvestment_quality",
    ])
    def test_rejects_below_zero(self, field):
        kwargs = {
            "buyback_quality": 5, "capital_return": 5,
            "acquisition_quality": 5, "debt_management": 5, "reinvestment_quality": 5,
        }
        kwargs[field] = -0.01
        with pytest.raises(ValidationError):
            CapitalAllocationScore(**kwargs)

    @pytest.mark.parametrize("field", [
        "buyback_quality", "capital_return",
        "acquisition_quality", "debt_management", "reinvestment_quality",
    ])
    def test_rejects_above_ten(self, field):
        kwargs = {
            "buyback_quality": 5, "capital_return": 5,
            "acquisition_quality": 5, "debt_management": 5, "reinvestment_quality": 5,
        }
        kwargs[field] = 10.01
        with pytest.raises(ValidationError):
            CapitalAllocationScore(**kwargs)

    def test_single_field_nonzero(self):
        """Only one field at 10 => weighted = 10/5*10 = 20."""
        s = CapitalAllocationScore(
            buyback_quality=10, capital_return=0,
            acquisition_quality=0, debt_management=0, reinvestment_quality=0,
        )
        assert s.weighted_score == pytest.approx(20.0)
