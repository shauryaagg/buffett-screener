import logging
from typing import Dict, Optional, List, Tuple
from edgar import Company, set_identity
from config.settings import EDGAR_IDENTITY
from core.database import Database

logger = logging.getLogger(__name__)


class EdgarClient:
    """Extracts 10-K sections using edgartools with database caching."""

    SECTIONS = {
        "item_1": "Item 1",
        "item_1a": "Item 1A",
        "item_7": "Item 7",
    }

    def __init__(self, db: Database):
        self.db = db
        if EDGAR_IDENTITY:
            set_identity(EDGAR_IDENTITY)

    def get_company(self, ticker: str) -> Optional[Company]:
        """Get a Company object from edgartools."""
        try:
            return Company(ticker)
        except Exception as e:
            logger.error(f"Cannot find company for {ticker}: {e}")
            return None

    def get_company_sic(self, ticker: str) -> Optional[int]:
        """Get the SIC code for a company."""
        company = self.get_company(ticker)
        if company and hasattr(company, 'sic'):
            return int(company.sic) if company.sic else None
        return None

    def has_tenk(self, ticker: str) -> bool:
        """Check if the company has at least one 10-K filing."""
        company = self.get_company(ticker)
        if not company:
            return False
        try:
            filings = company.get_filings(form="10-K")
            return len(filings) > 0
        except Exception:
            return False

    def get_tenk_sections(self, ticker: str, filing_date: str = None) -> Dict[str, str]:
        """
        Extract Item 1, Item 1A, Item 7 from the latest (or specified) 10-K.
        Returns dict like {"item_1": "text...", "item_1a": "text...", "item_7": "text..."}
        Caches to database.
        """
        # Try cache first for each section
        cached = {}
        all_cached = True
        for key in self.SECTIONS:
            text = self.db.load_tenk_cache(ticker, key)
            if text:
                cached[key] = text
            else:
                all_cached = False

        if all_cached and cached:
            logger.info(f"  {ticker}: all 10-K sections loaded from cache")
            return cached

        # Fetch from EDGAR
        company = self.get_company(ticker)
        if not company:
            return {}

        try:
            filings = company.get_filings(form="10-K")
            if not filings or len(filings) == 0:
                logger.warning(f"  {ticker}: no 10-K filings found")
                return {}

            filing = filings[0]  # Most recent
            accession = filing.accession_no if hasattr(filing, 'accession_no') else str(filing)
            f_date = str(filing.filing_date) if hasattr(filing, 'filing_date') else ""

            logger.info(f"  {ticker}: parsing 10-K from {f_date}")
            tenk = filing.obj()

            result = {}
            for key, section_name in self.SECTIONS.items():
                try:
                    text = tenk[section_name]
                    if text and isinstance(text, str) and len(text.strip()) > 100:
                        result[key] = text
                        self.db.save_tenk_cache(ticker, f_date, accession, key, text)
                    else:
                        # Try getting text representation
                        text_str = str(text) if text else ""
                        if len(text_str) > 100:
                            result[key] = text_str
                            self.db.save_tenk_cache(ticker, f_date, accession, key, text_str)
                        else:
                            logger.warning(f"  {ticker}: section {section_name} too short or empty")
                except Exception as e:
                    logger.warning(f"  {ticker}: error extracting {section_name}: {e}")

            return result

        except Exception as e:
            logger.error(f"  {ticker}: error processing 10-K: {e}")
            return {}

    def get_historical_mda(self, ticker: str, years: int = 10) -> List[Tuple[str, str]]:
        """
        Get MD&A (Item 7) from the last N 10-K filings.
        Returns: [(filing_date, mda_text), ...]
        """
        company = self.get_company(ticker)
        if not company:
            return []

        results = []
        try:
            filings = company.get_filings(form="10-K")
            count = min(years, len(filings))

            for i in range(count):
                filing = filings[i]
                f_date = str(filing.filing_date) if hasattr(filing, 'filing_date') else f"year_{i}"
                accession = filing.accession_no if hasattr(filing, 'accession_no') else str(filing)

                # Check cache
                cached = self.db.load_tenk_cache(ticker, "item_7", accession)
                if cached:
                    results.append((f_date, cached))
                    continue

                try:
                    tenk = filing.obj()
                    mda = tenk["Item 7"]
                    if mda:
                        text = str(mda) if not isinstance(mda, str) else mda
                        if len(text) > 100:
                            self.db.save_tenk_cache(ticker, f_date, accession, "item_7", text)
                            results.append((f_date, text))
                except Exception as e:
                    logger.warning(f"  {ticker}: error getting Item 7 for year {f_date}: {e}")
        except Exception as e:
            logger.error(f"  {ticker}: error getting historical MD&A: {e}")

        return results
