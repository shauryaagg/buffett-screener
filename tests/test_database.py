"""Tests for core/database.py — CRUD operations against an in-memory SQLite DB."""
import pytest
from datetime import datetime

from core.models import PipelineState, PipelineStatus
from core.database import Database


# The `memory_db` fixture is defined in conftest.py


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:

    def test_creates_all_tables(self, memory_db):
        """init_db should create pipeline_state, analysis_results, and tenk_cache."""
        with memory_db._conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        table_names = {row["name"] for row in tables}
        assert "pipeline_state" in table_names
        assert "analysis_results" in table_names
        assert "tenk_cache" in table_names

    def test_idempotent(self, memory_db):
        """Calling init_db twice should not error (CREATE TABLE IF NOT EXISTS)."""
        memory_db.init_db()  # second call
        with memory_db._conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        assert len(tables) >= 3


# ---------------------------------------------------------------------------
# Pipeline State
# ---------------------------------------------------------------------------

class TestPipelineState:

    def test_save_and_load_roundtrip(self, memory_db):
        now = datetime(2025, 6, 15, 10, 30, 0)
        state = PipelineState(
            run_id="test-run-1",
            current_filter=2,
            current_ticker_idx=5,
            started_at=now,
            status=PipelineStatus.RUNNING,
            ticker_limit=100,
        )
        memory_db.save_pipeline_state(state)
        loaded = memory_db.load_pipeline_state("test-run-1")

        assert loaded is not None
        assert loaded.run_id == "test-run-1"
        assert loaded.current_filter == 2
        assert loaded.current_ticker_idx == 5
        assert loaded.started_at == now
        assert loaded.status == PipelineStatus.RUNNING
        assert loaded.ticker_limit == 100

    def test_load_nonexistent_returns_none(self, memory_db):
        assert memory_db.load_pipeline_state("does-not-exist") is None

    def test_save_replaces_existing(self, memory_db):
        s1 = PipelineState(run_id="r1", current_filter=1)
        memory_db.save_pipeline_state(s1)

        s2 = PipelineState(run_id="r1", current_filter=3)
        memory_db.save_pipeline_state(s2)

        loaded = memory_db.load_pipeline_state("r1")
        assert loaded.current_filter == 3

    def test_save_with_none_dates(self, memory_db):
        state = PipelineState(run_id="r2")
        memory_db.save_pipeline_state(state)
        loaded = memory_db.load_pipeline_state("r2")
        assert loaded.started_at is None
        assert loaded.paused_at is None
        assert loaded.completed_at is None


class TestUpdatePipelineStatus:

    def test_updates_status(self, memory_db):
        state = PipelineState(run_id="u1", status=PipelineStatus.RUNNING)
        memory_db.save_pipeline_state(state)

        memory_db.update_pipeline_status("u1", PipelineStatus.PAUSED)
        loaded = memory_db.load_pipeline_state("u1")
        assert loaded.status == PipelineStatus.PAUSED

    def test_updates_additional_fields(self, memory_db):
        state = PipelineState(run_id="u2", current_filter=1, current_ticker_idx=0)
        memory_db.save_pipeline_state(state)

        now = datetime(2025, 7, 1, 12, 0, 0)
        memory_db.update_pipeline_status(
            "u2", PipelineStatus.COMPLETED,
            current_filter=4,
            current_ticker_idx=99,
            completed_at=now,
        )
        loaded = memory_db.load_pipeline_state("u2")
        assert loaded.status == PipelineStatus.COMPLETED
        assert loaded.current_filter == 4
        assert loaded.current_ticker_idx == 99
        assert loaded.completed_at == now


# ---------------------------------------------------------------------------
# Analysis Results
# ---------------------------------------------------------------------------

class TestAnalysisResults:

    def test_insert_and_load(self, memory_db):
        memory_db.save_analysis_result(
            "AAPL", "run1",
            f1_passed=True,
            f1_reason="SIC ok",
        )
        row = memory_db.load_analysis_result("AAPL", "run1")
        assert row is not None
        assert row["ticker"] == "AAPL"
        assert row["f1_passed"] == 1  # SQLite stores booleans as int
        assert row["f1_reason"] == "SIC ok"

    def test_upsert_updates_existing(self, memory_db):
        memory_db.save_analysis_result("AAPL", "run1", f1_passed=True, f1_reason="ok")
        memory_db.save_analysis_result("AAPL", "run1", f2_passed=False, f2_score=42.0)

        row = memory_db.load_analysis_result("AAPL", "run1")
        assert row["f1_passed"] == 1  # first insert still present
        assert row["f2_passed"] == 0
        assert row["f2_score"] == pytest.approx(42.0)

    def test_load_nonexistent_returns_none(self, memory_db):
        assert memory_db.load_analysis_result("NOPE", "run1") is None

    def test_save_no_kwargs_on_existing_does_nothing(self, memory_db):
        """When update_cols is empty, the method returns early."""
        memory_db.save_analysis_result("X", "run1", f1_passed=True)
        # Call with no valid analysis columns — should be a no-op
        memory_db.save_analysis_result("X", "run1")
        row = memory_db.load_analysis_result("X", "run1")
        assert row["f1_passed"] == 1


# ---------------------------------------------------------------------------
# get_passed_tickers
# ---------------------------------------------------------------------------

class TestGetPassedTickers:

    def test_returns_only_passed(self, memory_db):
        memory_db.save_analysis_result("A", "r1", f1_passed=True)
        memory_db.save_analysis_result("B", "r1", f1_passed=False)
        memory_db.save_analysis_result("C", "r1", f1_passed=True)

        passed = memory_db.get_passed_tickers("r1", 1)
        assert sorted(passed) == ["A", "C"]

    def test_empty_when_none_passed(self, memory_db):
        memory_db.save_analysis_result("A", "r1", f1_passed=False)
        assert memory_db.get_passed_tickers("r1", 1) == []

    def test_different_filter_nums(self, memory_db):
        memory_db.save_analysis_result("A", "r1", f1_passed=True, f2_passed=False)
        assert memory_db.get_passed_tickers("r1", 1) == ["A"]
        assert memory_db.get_passed_tickers("r1", 2) == []

    def test_scoped_to_run_id(self, memory_db):
        memory_db.save_analysis_result("A", "r1", f1_passed=True)
        memory_db.save_analysis_result("A", "r2", f1_passed=False)

        assert memory_db.get_passed_tickers("r1", 1) == ["A"]
        assert memory_db.get_passed_tickers("r2", 1) == []


# ---------------------------------------------------------------------------
# 10-K Cache
# ---------------------------------------------------------------------------

class TestTenkCache:

    def test_save_and_load_with_accession(self, memory_db):
        memory_db.save_tenk_cache(
            "AAPL", "2024-10-31", "0001234-24-000001", "item_1",
            "This is the business description..."
        )
        text = memory_db.load_tenk_cache("AAPL", "item_1", accession="0001234-24-000001")
        assert text == "This is the business description..."

    def test_load_without_accession_returns_most_recent(self, memory_db):
        memory_db.save_tenk_cache(
            "AAPL", "2023-10-31", "acc-2023", "item_7", "Old MD&A"
        )
        memory_db.save_tenk_cache(
            "AAPL", "2024-10-31", "acc-2024", "item_7", "New MD&A"
        )
        text = memory_db.load_tenk_cache("AAPL", "item_7")
        assert text == "New MD&A"

    def test_load_nonexistent_returns_none(self, memory_db):
        assert memory_db.load_tenk_cache("AAPL", "item_1") is None

    def test_replace_on_duplicate(self, memory_db):
        memory_db.save_tenk_cache("X", "2024-01-01", "acc1", "item_1", "version1")
        memory_db.save_tenk_cache("X", "2024-01-01", "acc1", "item_1", "version2")
        text = memory_db.load_tenk_cache("X", "item_1", accession="acc1")
        assert text == "version2"

    def test_token_estimate_stored(self, memory_db):
        content = "a" * 400  # 400 chars => ~100 tokens
        memory_db.save_tenk_cache("T", "2024-01-01", "acc", "item_1", content)
        with memory_db._conn() as conn:
            row = conn.execute(
                "SELECT token_estimate FROM tenk_cache WHERE ticker = ?", ("T",)
            ).fetchone()
        assert row["token_estimate"] == 100


# ---------------------------------------------------------------------------
# get_run_summary / get_run_results
# ---------------------------------------------------------------------------

class TestRunSummaryAndResults:

    def test_run_summary_counts(self, memory_db):
        memory_db.save_analysis_result("A", "r1", f1_passed=True, f2_passed=True, final_passed=True)
        memory_db.save_analysis_result("B", "r1", f1_passed=True, f2_passed=False, final_passed=False)
        memory_db.save_analysis_result("C", "r1", f1_passed=False, final_passed=False)

        summary = memory_db.get_run_summary("r1")
        assert summary["total"] == 3
        assert summary["f1_passed"] == 2
        assert summary["f2_passed"] == 1
        assert summary["final_passed"] == 1

    def test_run_summary_empty_run(self, memory_db):
        """When no rows match, COUNT returns 0 but SUM returns None (SQL NULL)."""
        summary = memory_db.get_run_summary("empty")
        assert summary["total"] == 0
        # NOTE: SQLite SUM over zero rows returns NULL, not 0.
        # The production code in _log_run_summary uses .get('f1_passed', 0) to handle this.
        assert summary["f1_passed"] is None

    def test_get_run_results_returns_all(self, memory_db):
        memory_db.save_analysis_result("A", "r1", f1_passed=True)
        memory_db.save_analysis_result("B", "r1", f1_passed=False)
        results = memory_db.get_run_results("r1")
        assert len(results) == 2
        tickers = {r["ticker"] for r in results}
        assert tickers == {"A", "B"}

    def test_get_run_results_empty(self, memory_db):
        assert memory_db.get_run_results("nope") == []
