"""Tests for core/pipeline.py — rate limit detection and get_status."""
import pytest
import tempfile
import os
from unittest.mock import MagicMock, patch
from datetime import datetime
from core.database import Database
from core.models import PipelineState, PipelineStatus
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
