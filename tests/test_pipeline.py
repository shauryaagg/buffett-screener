"""Tests for core/pipeline.py — rate limit detection, get_status, and run_single bypass_filters."""
import pytest
import tempfile
import os
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
from core.database import Database
from core.models import CompanyInfo, FilterResult, FullAnalysis, PipelineState, PipelineStatus
from core.pipeline import _is_rate_limit_error


# ===========================================================================
# _is_rate_limit_error
# ===========================================================================

class TestIsRateLimitError:

    @pytest.mark.parametrize("msg", [
        "rate limit exceeded",
        "Error 429: Too Many Requests",
        "The server is overloaded, please retry",
        "Rate Limit Exceeded for model",
        "too many requests",
    ])
    def test_detects_rate_limit(self, msg):
        exc = Exception(msg)
        assert _is_rate_limit_error(exc) is True

    @pytest.mark.parametrize("msg", [
        "invalid token",
        "auth token expired",
        "connection timeout",
        "Internal server error",
        "KeyError: 'score'",
        "",
        # SDK parse errors containing "rate_limit" should NOT match
        "Unknown message type: rate_limit_event",
    ])
    def test_rejects_non_rate_limit(self, msg):
        exc = Exception(msg)
        assert _is_rate_limit_error(exc) is False

    def test_parse_error_never_matches(self):
        """MessageParseError with 'rate_limit' in the message is NOT a real rate limit."""
        # Simulate the SDK's MessageParseError
        class MessageParseError(Exception):
            pass
        exc = MessageParseError("Unknown message type: rate_limit_event")
        assert _is_rate_limit_error(exc) is False

    def test_case_insensitive(self):
        assert _is_rate_limit_error(Exception("429 Too Many Requests")) is True
        assert _is_rate_limit_error(Exception("OVERLOADED")) is True
        assert _is_rate_limit_error(Exception("Rate Limit Exceeded")) is True


# ===========================================================================
# Pipeline.get_status
# ===========================================================================

class TestPipelineGetStatus:
    """Test get_status without needing external services by mocking Pipeline.__init__."""

    def _make_db(self, tmp_path):
        db_file = str(tmp_path / "pipeline_test.db")
        db = Database(db_path=db_file)
        db.init_db()
        return db

    def _make_pipeline(self, db):
        """Create a Pipeline instance with all external deps mocked out."""
        with patch("core.pipeline._try_create_market_data", return_value=None), \
             patch("core.pipeline.EdgarClient"), \
             patch("core.pipeline.FinancialDataService"), \
             patch("core.pipeline.BusinessTypeFilter"), \
             patch("core.pipeline.ManagementQualityFilter"), \
             patch("core.pipeline.ValuationFilter"), \
             patch("core.pipeline.CapitalAllocationFilter"):
            from core.pipeline import Pipeline
            return Pipeline(db)

    def test_no_run_id_returns_error(self, tmp_path):
        db = self._make_db(tmp_path)
        pipeline = self._make_pipeline(db)

        status = pipeline.get_status()
        assert "error" in status
        assert "no run_id" in status["error"].lower() or "No run_id" in status["error"]

    def test_nonexistent_run_id_returns_error(self, tmp_path):
        db = self._make_db(tmp_path)
        pipeline = self._make_pipeline(db)

        status = pipeline.get_status(run_id="ghost")
        assert "error" in status
        assert "ghost" in status["error"]

    def test_existing_run_id_returns_status(self, tmp_path):
        db = self._make_db(tmp_path)

        state = PipelineState(
            run_id="real-run",
            current_filter=2,
            current_ticker_idx=10,
            started_at=datetime(2025, 6, 1),
            status=PipelineStatus.RUNNING,
        )
        db.save_pipeline_state(state)
        db.save_analysis_result("A", "real-run", f1_passed=True)
        db.save_analysis_result("B", "real-run", f1_passed=False)

        pipeline = self._make_pipeline(db)
        status = pipeline.get_status(run_id="real-run")

        assert status["run_id"] == "real-run"
        assert status["status"] == "running"
        assert status["current_filter"] == 2
        assert status["summary"]["total"] == 2
        assert status["summary"]["f1_passed"] == 1


# ===========================================================================
# Pipeline.run_single — bypass_filters feature
# ===========================================================================

def _make_filter_result(passed: bool, reasoning: str = "", score: float = None) -> FilterResult:
    """Helper to build a FilterResult."""
    return FilterResult(passed=passed, reasoning=reasoning, score=score)


def _make_mock_filter(result: FilterResult) -> MagicMock:
    """Create a mock filter whose evaluate() returns the given FilterResult."""
    filt = MagicMock()
    filt.evaluate = AsyncMock(return_value=result)
    return filt


class TestRunSingleBypassFilters:
    """Tests for the bypass_filters parameter on Pipeline.run_single()."""

    DUMMY_COMPANY = CompanyInfo(ticker="TEST", name="Test Corp", market_cap=1e9, price=50.0)

    def _make_pipeline_with_filters(self, filter_results: list[FilterResult]):
        """
        Build a Pipeline with mocked-out constructor deps and custom filter results.

        filter_results: list of 4 FilterResult objects, one per filter.
        """
        with patch("core.pipeline._try_create_market_data", return_value=None), \
             patch("core.pipeline.EdgarClient"), \
             patch("core.pipeline.FinancialDataService"), \
             patch("core.pipeline.BusinessTypeFilter"), \
             patch("core.pipeline.ManagementQualityFilter"), \
             patch("core.pipeline.ValuationFilter"), \
             patch("core.pipeline.CapitalAllocationFilter"):
            from core.pipeline import Pipeline
            db = MagicMock()
            pipeline = Pipeline(db)

        # Replace the filters list with our mocks
        pipeline.filters = [_make_mock_filter(r) for r in filter_results]
        # Mock _build_single_company so it doesn't need MarketDataService
        pipeline._build_single_company = MagicMock(return_value=self.DUMMY_COMPANY)
        return pipeline

    # ------------------------------------------------------------------
    # Default behavior (bypass_filters=False): early exit on failure
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_default_f1_fail_skips_remaining_filters(self):
        """When bypass_filters=False and F1 fails, F2/F3/F4 are never called."""
        results = [
            _make_filter_result(False, "Not a Buffett business"),
            _make_filter_result(True, "Good management"),
            _make_filter_result(True, "Undervalued"),
            _make_filter_result(True, "Good capital allocation"),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=False)

        # F1 ran and populated
        assert analysis.f1_result is not None
        assert analysis.f1_result.passed is False

        # F2/F3/F4 never ran
        assert analysis.f2_result is None
        assert analysis.f3_result is None
        assert analysis.f4_result is None

        assert analysis.final_passed is False

        # Verify evaluate was only called on F1
        pipeline.filters[0].evaluate.assert_awaited_once()
        pipeline.filters[1].evaluate.assert_not_awaited()
        pipeline.filters[2].evaluate.assert_not_awaited()
        pipeline.filters[3].evaluate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_f2_fail_skips_f3_f4(self):
        """When bypass_filters=False and F2 fails, F3/F4 are not called."""
        results = [
            _make_filter_result(True, "Good business"),
            _make_filter_result(False, "Poor management"),
            _make_filter_result(True, "Undervalued"),
            _make_filter_result(True, "Good capital allocation"),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=False)

        assert analysis.f1_result is not None
        assert analysis.f1_result.passed is True
        assert analysis.f2_result is not None
        assert analysis.f2_result.passed is False
        assert analysis.f3_result is None
        assert analysis.f4_result is None
        assert analysis.final_passed is False

    @pytest.mark.asyncio
    async def test_default_f3_fail_skips_f4(self):
        """When bypass_filters=False and F3 fails, F4 is not called."""
        results = [
            _make_filter_result(True, "Good business"),
            _make_filter_result(True, "Good management"),
            _make_filter_result(False, "Overvalued"),
            _make_filter_result(True, "Good capital allocation"),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=False)

        assert analysis.f1_result is not None
        assert analysis.f2_result is not None
        assert analysis.f3_result is not None
        assert analysis.f3_result.passed is False
        assert analysis.f4_result is None
        assert analysis.final_passed is False

    @pytest.mark.asyncio
    async def test_default_all_pass(self):
        """When bypass_filters=False and all filters pass, final_passed is True."""
        results = [
            _make_filter_result(True, "Good business"),
            _make_filter_result(True, "Good management", score=8.0),
            _make_filter_result(True, "Undervalued"),
            _make_filter_result(True, "Good capital allocation", score=7.5),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=False)

        assert analysis.f1_result.passed is True
        assert analysis.f2_result.passed is True
        assert analysis.f3_result.passed is True
        assert analysis.f4_result.passed is True
        assert analysis.final_passed is True

    # ------------------------------------------------------------------
    # bypass_filters=True: all filters run regardless of failures
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_bypass_f1_fail_runs_all_filters(self):
        """When bypass_filters=True and F1 fails, F2/F3/F4 still run."""
        results = [
            _make_filter_result(False, "Not a Buffett business"),
            _make_filter_result(True, "Good management", score=7.0),
            _make_filter_result(True, "Undervalued"),
            _make_filter_result(True, "Good capital allocation", score=8.0),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=True)

        # All 4 results are populated
        assert analysis.f1_result is not None
        assert analysis.f2_result is not None
        assert analysis.f3_result is not None
        assert analysis.f4_result is not None

        # F1 failed, others passed
        assert analysis.f1_result.passed is False
        assert analysis.f2_result.passed is True
        assert analysis.f3_result.passed is True
        assert analysis.f4_result.passed is True

        # All 4 filters were actually invoked
        for i in range(4):
            pipeline.filters[i].evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bypass_f2_fail_runs_f3_f4(self):
        """When bypass_filters=True and F2 fails, F3/F4 still run."""
        results = [
            _make_filter_result(True, "Good business"),
            _make_filter_result(False, "Poor management", score=3.0),
            _make_filter_result(True, "Undervalued"),
            _make_filter_result(True, "Good capital allocation", score=7.0),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=True)

        assert analysis.f1_result.passed is True
        assert analysis.f2_result is not None
        assert analysis.f2_result.passed is False
        assert analysis.f3_result is not None
        assert analysis.f3_result.passed is True
        assert analysis.f4_result is not None
        assert analysis.f4_result.passed is True

        for i in range(4):
            pipeline.filters[i].evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bypass_f3_fail_runs_f4(self):
        """When bypass_filters=True and F3 fails, F4 still runs."""
        results = [
            _make_filter_result(True, "Good business"),
            _make_filter_result(True, "Good management", score=7.0),
            _make_filter_result(False, "Overvalued"),
            _make_filter_result(True, "Good capital allocation", score=8.0),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=True)

        assert analysis.f3_result.passed is False
        assert analysis.f4_result is not None
        assert analysis.f4_result.passed is True

        for i in range(4):
            pipeline.filters[i].evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bypass_all_fail_still_runs_everything(self):
        """When bypass_filters=True and ALL filters fail, all 4 still execute."""
        results = [
            _make_filter_result(False, "Bad business"),
            _make_filter_result(False, "Bad management", score=2.0),
            _make_filter_result(False, "Overvalued"),
            _make_filter_result(False, "Poor capital allocation", score=1.0),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=True)

        assert analysis.f1_result is not None and analysis.f1_result.passed is False
        assert analysis.f2_result is not None and analysis.f2_result.passed is False
        assert analysis.f3_result is not None and analysis.f3_result.passed is False
        assert analysis.f4_result is not None and analysis.f4_result.passed is False

        for i in range(4):
            pipeline.filters[i].evaluate.assert_awaited_once()

    # ------------------------------------------------------------------
    # final_passed correctness in bypass mode
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_bypass_final_passed_false_when_any_filter_fails(self):
        """In bypass mode, final_passed is False if ANY filter failed."""
        results = [
            _make_filter_result(False, "Bad business"),
            _make_filter_result(True, "Good management", score=8.0),
            _make_filter_result(True, "Undervalued"),
            _make_filter_result(True, "Good capital allocation", score=9.0),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=True)

        assert analysis.final_passed is False

    @pytest.mark.asyncio
    async def test_bypass_final_passed_false_when_only_f4_fails(self):
        """In bypass mode, final_passed is False even if only the last filter fails."""
        results = [
            _make_filter_result(True, "Good business"),
            _make_filter_result(True, "Good management", score=8.0),
            _make_filter_result(True, "Undervalued"),
            _make_filter_result(False, "Poor capital allocation", score=3.0),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=True)

        assert analysis.final_passed is False

    @pytest.mark.asyncio
    async def test_bypass_final_passed_true_when_all_pass(self):
        """In bypass mode, final_passed is True only when all 4 filters pass."""
        results = [
            _make_filter_result(True, "Good business"),
            _make_filter_result(True, "Good management", score=8.0),
            _make_filter_result(True, "Undervalued"),
            _make_filter_result(True, "Good capital allocation", score=9.0),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=True)

        assert analysis.final_passed is True

    @pytest.mark.asyncio
    async def test_bypass_final_passed_false_when_all_fail(self):
        """In bypass mode, final_passed is False when all 4 filters fail."""
        results = [
            _make_filter_result(False, "Bad business"),
            _make_filter_result(False, "Bad management", score=2.0),
            _make_filter_result(False, "Overvalued"),
            _make_filter_result(False, "Poor allocation", score=1.0),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=True)

        assert analysis.final_passed is False

    # ------------------------------------------------------------------
    # bypass_filters defaults to False (keyword arg)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_bypass_defaults_to_false(self):
        """Calling run_single without bypass_filters uses the default (False) behavior."""
        results = [
            _make_filter_result(False, "Bad business"),
            _make_filter_result(True, "Good management"),
            _make_filter_result(True, "Undervalued"),
            _make_filter_result(True, "Good capital allocation"),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        # Call WITHOUT bypass_filters keyword
        analysis = await pipeline.run_single("TEST")

        # Should early-exit on F1 failure
        assert analysis.f1_result is not None
        assert analysis.f2_result is None
        assert analysis.final_passed is False

    # ------------------------------------------------------------------
    # Return type and company data
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_bypass_returns_full_analysis_with_correct_company(self):
        """The returned FullAnalysis has the correct company info."""
        results = [
            _make_filter_result(False, "Bad"),
            _make_filter_result(True, "Good"),
            _make_filter_result(True, "Good"),
            _make_filter_result(True, "Good"),
        ]
        pipeline = self._make_pipeline_with_filters(results)

        analysis = await pipeline.run_single("TEST", bypass_filters=True)

        assert isinstance(analysis, FullAnalysis)
        assert analysis.company.ticker == "TEST"
        assert analysis.company.name == "Test Corp"
        assert analysis.analyzed_at is not None
