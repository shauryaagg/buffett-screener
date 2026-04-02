"""Tests for MarketDataService.get_single_quote() validation logic.

The improved validation rejects tickers where yfinance returns empty/non-dict info
or info lacking all of: marketCap, currentPrice, regularMarketPrice.
Previously the check was `trailingPegRatio is None and marketCap is None`, which
incorrectly rejected valid tickers like HIFS that have no trailingPegRatio.

All yfinance calls are mocked — no real network calls.
"""
import pytest
from unittest.mock import patch, MagicMock
from data.market_data import MarketDataService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service():
    """Create MarketDataService with mocked os.makedirs."""
    with patch("data.market_data.os.makedirs"):
        return MarketDataService()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetSingleQuoteValidation:

    @patch("data.market_data.yf.Ticker")
    def test_valid_ticker_with_market_cap_no_trailing_peg(self, mock_ticker_cls):
        """HIFS bug: ticker has marketCap but no trailingPegRatio.
        Old validation rejected this; new validation should accept it."""
        mock_ticker_cls.return_value.info = {
            "marketCap": 350_000_000,
            "currentPrice": 22.50,
            "shortName": "Hingham Savings",
            "exchange": "NASDAQ",
            # No trailingPegRatio key at all
        }
        svc = _make_service()
        result = svc.get_single_quote("HIFS")

        assert result is not None
        assert result["price"] == 22.50
        assert result["market_cap"] == 350_000_000
        assert result["name"] == "Hingham Savings"

    @patch("data.market_data.yf.Ticker")
    def test_invalid_ticker_returns_none_when_info_is_empty(self, mock_ticker_cls):
        """yfinance returns empty dict for some invalid tickers."""
        mock_ticker_cls.return_value.info = {}
        svc = _make_service()
        result = svc.get_single_quote("ZZZZZ")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_invalid_ticker_returns_none_when_info_is_none(self, mock_ticker_cls):
        """yfinance sometimes returns None for info."""
        mock_ticker_cls.return_value.info = None
        svc = _make_service()
        result = svc.get_single_quote("BOGUS")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_info_not_a_dict_returns_none(self, mock_ticker_cls):
        """If info is not a dict (e.g. a string or list), return None."""
        mock_ticker_cls.return_value.info = "not a dict"
        svc = _make_service()
        result = svc.get_single_quote("WEIRD")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_info_is_list_returns_none(self, mock_ticker_cls):
        mock_ticker_cls.return_value.info = [1, 2, 3]
        svc = _make_service()
        result = svc.get_single_quote("WEIRD2")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_no_market_cap_but_has_current_price_returns_none(self, mock_ticker_cls):
        """Ticker with no marketCap defaults to 0, which is now rejected by
        the market_cap <= 0 guard."""
        mock_ticker_cls.return_value.info = {
            "currentPrice": 15.00,
            "shortName": "SmallCo",
            "exchange": "NYSE",
            # No marketCap — defaults to 0
        }
        svc = _make_service()
        result = svc.get_single_quote("SMCO")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_no_market_cap_but_has_regular_market_price_returns_none(self, mock_ticker_cls):
        """Ticker with regularMarketPrice but no marketCap — market_cap defaults
        to 0, which is rejected by the market_cap <= 0 guard."""
        mock_ticker_cls.return_value.info = {
            "regularMarketPrice": 8.50,
            "shortName": "RegCo",
            "exchange": "AMEX",
        }
        svc = _make_service()
        result = svc.get_single_quote("REGCO")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_no_price_data_at_all_returns_none(self, mock_ticker_cls):
        """Info dict with none of the required fields returns None."""
        mock_ticker_cls.return_value.info = {
            "shortName": "GhostCo",
            "exchange": "NYSE",
            # No marketCap, no currentPrice, no regularMarketPrice
        }
        svc = _make_service()
        result = svc.get_single_quote("GHOST")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_zero_price_returns_none(self, mock_ticker_cls):
        """Even if marketCap is present, price==0 should return None."""
        mock_ticker_cls.return_value.info = {
            "marketCap": 100_000_000,
            "currentPrice": 0,
            "shortName": "ZeroCo",
            "exchange": "NYSE",
        }
        svc = _make_service()
        result = svc.get_single_quote("ZERO")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_negative_price_returns_none(self, mock_ticker_cls):
        """Negative price should be rejected."""
        mock_ticker_cls.return_value.info = {
            "marketCap": 100_000_000,
            "currentPrice": -5.0,
            "shortName": "NegCo",
            "exchange": "NYSE",
        }
        svc = _make_service()
        result = svc.get_single_quote("NEG")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_zero_market_cap_returns_none(self, mock_ticker_cls):
        """market_cap == 0 should be rejected by the market_cap <= 0 guard."""
        mock_ticker_cls.return_value.info = {
            "marketCap": 0,
            "currentPrice": 10.0,
            "shortName": "ZeroMktCap",
            "exchange": "NYSE",
        }
        svc = _make_service()
        result = svc.get_single_quote("ZMKT")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_negative_market_cap_returns_none(self, mock_ticker_cls):
        """Negative market_cap should be rejected."""
        mock_ticker_cls.return_value.info = {
            "marketCap": -500_000,
            "currentPrice": 10.0,
            "shortName": "NegMktCap",
            "exchange": "NYSE",
        }
        svc = _make_service()
        result = svc.get_single_quote("NMKT")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_yfinance_exception_returns_none(self, mock_ticker_cls):
        """If yfinance throws, return None gracefully."""
        mock_ticker_cls.return_value.info = property(
            fget=lambda self: (_ for _ in ()).throw(ConnectionError("timeout"))
        )
        # Simpler: make .info raise
        type(mock_ticker_cls.return_value).info = property(
            lambda self: (_ for _ in ()).throw(ConnectionError("timeout"))
        )
        svc = _make_service()
        result = svc.get_single_quote("TIMEOUT")

        assert result is None

    @patch("data.market_data.yf.Ticker")
    def test_has_market_cap_and_price_returns_complete_dict(self, mock_ticker_cls):
        """Happy path: all data present returns complete result dict."""
        mock_ticker_cls.return_value.info = {
            "marketCap": 2_000_000_000,
            "currentPrice": 50.0,
            "shortName": "GoodCo",
            "longName": "Good Company Inc",
            "exchange": "NASDAQ",
        }
        svc = _make_service()
        result = svc.get_single_quote("GOOD")

        assert result == {
            "price": 50.0,
            "market_cap": 2_000_000_000,
            "name": "GoodCo",
            "exchange": "NASDAQ",
        }

    @patch("data.market_data.yf.Ticker")
    def test_falls_back_to_long_name_when_short_name_missing(self, mock_ticker_cls):
        """When shortName is absent, longName is used."""
        mock_ticker_cls.return_value.info = {
            "marketCap": 500_000_000,
            "currentPrice": 30.0,
            "longName": "Fallback Name Inc",
            "exchange": "NYSE",
        }
        svc = _make_service()
        result = svc.get_single_quote("FALL")

        assert result["name"] == "Fallback Name Inc"

    @patch("data.market_data.yf.Ticker")
    def test_falls_back_to_regular_market_price_when_current_price_missing(self, mock_ticker_cls):
        """currentPrice absent => regularMarketPrice used."""
        mock_ticker_cls.return_value.info = {
            "marketCap": 300_000_000,
            "regularMarketPrice": 12.0,
            "shortName": "AltPrice",
            "exchange": "NYSE",
        }
        svc = _make_service()
        result = svc.get_single_quote("ALT")

        assert result["price"] == 12.0
