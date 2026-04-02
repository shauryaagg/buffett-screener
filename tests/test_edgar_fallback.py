"""Tests for EdgarClient.get_company_info_fallback() in data/edgar_client.py.

This method fetches longBusinessSummary, sector, and industry from yfinance
as a fallback when EDGAR lookups fail. All yfinance and edgar calls are mocked.
"""
import pytest
from unittest.mock import patch, MagicMock
from data.edgar_client import EdgarClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(tmp_path):
    """Create an EdgarClient with a real (temp) database and mocked EDGAR identity."""
    from core.database import Database

    db_file = str(tmp_path / "edgar_test.db")
    db = Database(db_path=db_file)
    db.init_db()
    with patch("data.edgar_client.set_identity"), \
         patch("data.edgar_client.EDGAR_IDENTITY", "test@example.com"):
        return EdgarClient(db)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetCompanyInfoFallback:

    @patch("data.edgar_client.yf.Ticker")
    def test_returns_yfinance_data_when_available(self, mock_ticker_cls, tmp_path):
        """Happy path: yfinance returns all three fields."""
        mock_ticker_cls.return_value.info = {
            "longBusinessSummary": "A company that manufactures widgets.",
            "sector": "Industrials",
            "industry": "Machinery",
            "marketCap": 500_000_000,
        }
        client = _make_client(tmp_path)
        result = client.get_company_info_fallback("WIDG")

        assert result["longBusinessSummary"] == "A company that manufactures widgets."
        assert result["sector"] == "Industrials"
        assert result["industry"] == "Machinery"
        # Should NOT include extra keys
        assert "marketCap" not in result

    @patch("data.edgar_client.yf.Ticker")
    def test_returns_partial_data_when_some_fields_missing(self, mock_ticker_cls, tmp_path):
        """If only longBusinessSummary is present, return just that."""
        mock_ticker_cls.return_value.info = {
            "longBusinessSummary": "We sell things.",
            # No sector or industry
        }
        client = _make_client(tmp_path)
        result = client.get_company_info_fallback("SELL")

        assert result == {"longBusinessSummary": "We sell things."}

    @patch("data.edgar_client.yf.Ticker")
    def test_returns_empty_dict_when_yfinance_has_no_relevant_data(self, mock_ticker_cls, tmp_path):
        """Info dict exists but has none of the three target fields."""
        mock_ticker_cls.return_value.info = {
            "marketCap": 100_000_000,
            "currentPrice": 10.0,
        }
        client = _make_client(tmp_path)
        result = client.get_company_info_fallback("NOPE")

        assert result == {}

    @patch("data.edgar_client.yf.Ticker")
    def test_returns_empty_dict_when_info_is_none(self, mock_ticker_cls, tmp_path):
        """yfinance returning None for info."""
        mock_ticker_cls.return_value.info = None
        client = _make_client(tmp_path)
        result = client.get_company_info_fallback("NULL")

        assert result == {}

    @patch("data.edgar_client.yf.Ticker")
    def test_returns_empty_dict_when_info_is_not_dict(self, mock_ticker_cls, tmp_path):
        """yfinance returning a non-dict for info."""
        mock_ticker_cls.return_value.info = "string value"
        client = _make_client(tmp_path)
        result = client.get_company_info_fallback("STR")

        assert result == {}

    @patch("data.edgar_client.yf.Ticker")
    def test_returns_empty_dict_when_info_is_empty(self, mock_ticker_cls, tmp_path):
        """yfinance returning empty dict."""
        mock_ticker_cls.return_value.info = {}
        client = _make_client(tmp_path)
        result = client.get_company_info_fallback("EMPTY")

        assert result == {}

    @patch("data.edgar_client.yf.Ticker")
    def test_returns_empty_dict_on_exception(self, mock_ticker_cls, tmp_path):
        """yfinance raises an exception -> return empty dict, don't crash."""
        mock_ticker_cls.side_effect = Exception("network error")
        client = _make_client(tmp_path)
        result = client.get_company_info_fallback("ERR")

        assert result == {}

    @patch("data.edgar_client.yf.Ticker")
    def test_skips_fields_with_falsy_values(self, mock_ticker_cls, tmp_path):
        """Fields that are empty strings or None should be omitted."""
        mock_ticker_cls.return_value.info = {
            "longBusinessSummary": "",
            "sector": None,
            "industry": "Technology",
        }
        client = _make_client(tmp_path)
        result = client.get_company_info_fallback("HALF")

        # Empty string and None are falsy, so only industry is included
        assert result == {"industry": "Technology"}
