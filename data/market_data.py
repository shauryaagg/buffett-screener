import requests
import json
import os
import logging
from datetime import date
from typing import Dict, Optional
from config.settings import FMP_API_KEY, MARKET_CAP_MIN, MARKET_CAP_MAX, CACHE_DIR

logger = logging.getLogger(__name__)


class MarketDataService:
    """Fetches bulk market data from FMP API."""

    BASE_URL = "https://financialmodelingprep.com/api/v3"
    EXCHANGES = ["nyse", "nasdaq", "amex"]

    def __init__(self):
        if not FMP_API_KEY:
            raise ValueError("FMP_API_KEY not set. Set it in .env or environment.")
        self.api_key = FMP_API_KEY
        os.makedirs(CACHE_DIR, exist_ok=True)

    def _cache_path(self) -> str:
        """Daily cache file path."""
        return os.path.join(CACHE_DIR, f"market_data_{date.today().isoformat()}.json")

    def fetch_exchange_quotes(self, exchange: str) -> list:
        """Fetch all quotes for an exchange. Returns list of dicts with symbol, price, mktCap, name, exchange."""
        url = f"{self.BASE_URL}/quotes/{exchange}"
        resp = requests.get(url, params={"apikey": self.api_key}, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def fetch_all_prices(self, use_cache: bool = True) -> Dict[str, dict]:
        """
        Fetch all stock prices from NYSE, NASDAQ, AMEX. Filter to market cap range.
        Returns: {ticker: {price, market_cap, name, exchange}}
        Caches results for the day.
        """
        cache_path = self._cache_path()

        if use_cache and os.path.exists(cache_path):
            logger.info(f"Loading cached market data from {cache_path}")
            with open(cache_path, 'r') as f:
                return json.load(f)

        all_quotes = {}
        for exchange in self.EXCHANGES:
            try:
                logger.info(f"Fetching quotes for {exchange}...")
                quotes = self.fetch_exchange_quotes(exchange)
                for q in quotes:
                    symbol = q.get("symbol", "")
                    mkt_cap = q.get("marketCap") or q.get("mktCap") or 0
                    price = q.get("price", 0)
                    name = q.get("name", "")

                    if not symbol or not price or price <= 0:
                        continue
                    if mkt_cap < MARKET_CAP_MIN or mkt_cap > MARKET_CAP_MAX:
                        continue

                    if symbol not in all_quotes:
                        all_quotes[symbol] = {
                            "price": price,
                            "market_cap": mkt_cap,
                            "name": name,
                            "exchange": exchange.upper()
                        }
                logger.info(f"  {exchange}: got {len(quotes)} total quotes")
            except Exception as e:
                logger.error(f"Error fetching {exchange}: {e}")

        # Also try OTC
        try:
            logger.info("Fetching OTC quotes...")
            url = f"{self.BASE_URL}/quotes/otc"
            resp = requests.get(url, params={"apikey": self.api_key}, timeout=60)
            if resp.status_code == 200:
                otc_quotes = resp.json()
                for q in otc_quotes:
                    symbol = q.get("symbol", "")
                    mkt_cap = q.get("marketCap") or q.get("mktCap") or 0
                    price = q.get("price", 0)
                    if not symbol or not price or price <= 0:
                        continue
                    if mkt_cap < MARKET_CAP_MIN or mkt_cap > MARKET_CAP_MAX:
                        continue
                    if symbol not in all_quotes:
                        all_quotes[symbol] = {
                            "price": price,
                            "market_cap": mkt_cap,
                            "name": q.get("name", ""),
                            "exchange": "OTC"
                        }
                logger.info(f"  OTC: got {len(otc_quotes)} total quotes")
        except Exception as e:
            logger.warning(f"OTC fetch failed (non-critical): {e}")

        logger.info(f"Total stocks in ${MARKET_CAP_MIN/1e6:.0f}M-${MARKET_CAP_MAX/1e9:.0f}B range: {len(all_quotes)}")

        with open(cache_path, 'w') as f:
            json.dump(all_quotes, f)

        return all_quotes

    def get_price(self, ticker: str) -> Optional[dict]:
        """Get price data for a single ticker. Uses cached data if available."""
        all_prices = self.fetch_all_prices()
        return all_prices.get(ticker)

    def get_single_quote(self, ticker: str) -> Optional[dict]:
        """Fetch a single ticker quote directly from FMP (bypasses market cap filter)."""
        try:
            url = f"{self.BASE_URL}/quote/{ticker}"
            resp = requests.get(url, params={"apikey": self.api_key}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data:
                q = data[0]
                return {
                    "price": q.get("price", 0),
                    "market_cap": q.get("marketCap", 0),
                    "name": q.get("name", ""),
                    "exchange": q.get("exchange", "")
                }
        except Exception as e:
            logger.error(f"Error fetching single quote for {ticker}: {e}")
        return None
