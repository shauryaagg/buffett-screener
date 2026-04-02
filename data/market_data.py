"""Market data service using Yahoo Finance (yfinance) + edgartools for universe building."""
import json
import os
import logging
from datetime import date
from typing import Dict, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

from config.settings import MARKET_CAP_MIN, MARKET_CAP_MAX, CACHE_DIR

logger = logging.getLogger(__name__)


class MarketDataService:
    """Fetches market data from Yahoo Finance. Free, no API key needed."""

    def __init__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)

    def _cache_path(self) -> str:
        """Daily cache file path."""
        return os.path.join(CACHE_DIR, f"market_data_{date.today().isoformat()}.json")

    def get_single_quote(self, ticker: str) -> Optional[dict]:
        """Fetch price and market data for a single ticker."""
        try:
            t = yf.Ticker(ticker)
            info = t.info
            if not info or not isinstance(info, dict):
                return None
            # yfinance returns a mostly-empty dict for invalid tickers
            if not info.get("marketCap") and not info.get("currentPrice") and not info.get("regularMarketPrice"):
                return None

            price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
            market_cap = info.get("marketCap", 0)
            name = info.get("shortName") or info.get("longName", "")
            exchange = info.get("exchange", "")

            if not price or price <= 0:
                return None
            if not market_cap or market_cap <= 0:
                return None

            return {
                "price": price,
                "market_cap": market_cap,
                "name": name,
                "exchange": exchange,
            }
        except Exception as e:
            logger.debug(f"Error fetching quote for {ticker}: {e}")
            return None

    def _fetch_ticker_info(self, ticker: str) -> Optional[tuple]:
        """Fetch info for a single ticker. Returns (ticker, quote_dict) or None."""
        try:
            t = yf.Ticker(ticker)
            info = t.info
            if not info:
                return None

            price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
            market_cap = info.get("marketCap", 0)

            if not price or price <= 0 or not market_cap:
                return None
            if market_cap < MARKET_CAP_MIN or market_cap > MARKET_CAP_MAX:
                return None

            return (ticker, {
                "price": price,
                "market_cap": market_cap,
                "name": info.get("shortName") or info.get("longName", ""),
                "exchange": info.get("exchange", ""),
            })
        except Exception:
            return None

    def fetch_all_prices(self, use_cache: bool = True) -> Dict[str, dict]:
        """
        Build the stock universe: edgartools ticker list + yfinance market data.
        Filters to $5M-$5B market cap.
        Returns: {ticker: {price, market_cap, name, exchange}}
        Caches results for the day.
        """
        cache_path = self._cache_path()

        if use_cache and os.path.exists(cache_path):
            logger.info(f"Loading cached market data from {cache_path}")
            with open(cache_path, 'r') as f:
                return json.load(f)

        try:
            from edgar import get_company_tickers, set_identity
            from config.settings import EDGAR_IDENTITY
            if EDGAR_IDENTITY:
                set_identity(EDGAR_IDENTITY)

            tickers_df = get_company_tickers()
            logger.info(f"Got {len(tickers_df)} tickers from EDGAR")

            # Filter to major exchanges
            valid_exchanges = {'NYSE', 'Nasdaq', 'AMEX', 'CBOE'}
            if 'exchange' in tickers_df.columns:
                filtered = tickers_df[tickers_df['exchange'].isin(valid_exchanges)]
            else:
                filtered = tickers_df

            ticker_list = filtered['ticker'].dropna().unique().tolist()
            logger.info(f"Checking {len(ticker_list)} tickers against market cap range...")

            # Use thread pool for parallel yfinance lookups
            all_quotes = {}
            batch_size = 500
            total = len(ticker_list)

            for batch_start in range(0, total, batch_size):
                batch = ticker_list[batch_start:batch_start + batch_size]
                logger.info(
                    f"  Batch {batch_start // batch_size + 1}: "
                    f"tickers {batch_start+1}-{min(batch_start+batch_size, total)} "
                    f"({len(all_quotes)} in range so far)"
                )

                with ThreadPoolExecutor(max_workers=10) as executor:
                    futures = {
                        executor.submit(self._fetch_ticker_info, t): t
                        for t in batch
                    }
                    for future in as_completed(futures):
                        result = future.result()
                        if result:
                            ticker, data = result
                            all_quotes[ticker] = data

            logger.info(
                f"Total stocks in ${MARKET_CAP_MIN/1e6:.0f}M-"
                f"${MARKET_CAP_MAX/1e9:.0f}B range: {len(all_quotes)}"
            )

            # Cache for the day
            with open(cache_path, 'w') as f:
                json.dump(all_quotes, f)

            return all_quotes

        except Exception as e:
            logger.error(f"Error building universe: {e}")
            return {}

    def get_price(self, ticker: str) -> Optional[dict]:
        """Get price data for a single ticker. Uses cached data if available."""
        cache_path = self._cache_path()
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    all_prices = json.load(f)
                if ticker in all_prices:
                    return all_prices[ticker]
            except Exception:
                pass
        return self.get_single_quote(ticker)
