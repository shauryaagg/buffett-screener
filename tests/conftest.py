"""Shared fixtures for buffett-screener tests."""
import os
import sys
import tempfile
import pytest

# Ensure project root is on the path so imports work regardless of cwd
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.database import Database
from core.models import CompanyInfo


@pytest.fixture
def memory_db(tmp_path):
    """Provide a fresh SQLite database with all tables created.

    Uses a temp file rather than :memory: because Database._conn() opens
    a new connection each time, and in-memory databases are per-connection.
    """
    db_file = str(tmp_path / "test.db")
    db = Database(db_path=db_file)
    db.init_db()
    return db


@pytest.fixture
def sample_company():
    """A typical CompanyInfo for testing."""
    return CompanyInfo(
        ticker="ACME",
        name="Acme Corp",
        sic=3559,
        industry="Industrial Machinery",
        market_cap=500_000_000,
        price=25.0,
        exchange="NASDAQ",
    )


@pytest.fixture
def sample_company_excluded_sic():
    """A company with an excluded SIC code (mining)."""
    return CompanyInfo(
        ticker="MINE",
        name="MiningCo",
        sic=1040,
        industry="Gold Mining",
        market_cap=200_000_000,
        price=10.0,
        exchange="NYSE",
    )


@pytest.fixture
def sample_company_no_sic():
    """A company with no SIC code."""
    return CompanyInfo(
        ticker="NOSIC",
        name="NoSicCo",
        sic=None,
        market_cap=100_000_000,
        price=5.0,
    )
