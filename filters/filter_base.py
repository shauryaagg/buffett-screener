"""Abstract base class for all pipeline filters."""
import logging
from abc import ABC, abstractmethod
from typing import List, Optional
from core.models import CompanyInfo, FilterResult, PipelineStatus
from core.database import Database

logger = logging.getLogger(__name__)


class FilterBase(ABC):
    """Base class for pipeline filters."""

    filter_num: int = 0
    filter_name: str = "base"

    def __init__(self, db: Database):
        self.db = db

    @abstractmethod
    async def evaluate(self, company: CompanyInfo) -> FilterResult:
        """
        Evaluate a single company against this filter.
        Returns a FilterResult with passed/failed, score, and reasoning.
        """
        ...

    async def run_batch(self, companies: List[CompanyInfo], run_id: str, start_idx: int = 0) -> List[tuple]:
        """
        Run this filter on a batch of companies. Saves results to DB.
        Returns list of (company, result) tuples.

        Args:
            companies: List of CompanyInfo to evaluate
            run_id: Pipeline run ID
            start_idx: Index to resume from (for pause/resume)
        """
        results = []
        total = len(companies)

        for i in range(start_idx, total):
            company = companies[i]
            logger.info(f"[F{self.filter_num}] ({i+1}/{total}) Evaluating {company.ticker}...")

            try:
                result = await self.evaluate(company)
                results.append((company, result))

                # Save to database
                self._save_result(company.ticker, run_id, result)

                # Update pipeline state
                self.db.update_pipeline_status(
                    run_id, PipelineStatus.RUNNING,
                    current_filter=self.filter_num,
                    current_ticker_idx=i + 1
                )

                status = "PASS" if result.passed else "FAIL"
                logger.info(f"  {company.ticker}: {status} (score={result.score})")

            except Exception as e:
                error_str = str(e).lower()
                if "rate_limit" in error_str or "token" in error_str or "overloaded" in error_str:
                    logger.warning(f"  Rate limit hit at {company.ticker}. Raising for pipeline to handle.")
                    raise

                logger.error(f"  {company.ticker}: ERROR - {e}")
                fail_result = FilterResult(passed=False, reasoning=f"Error: {e}")
                results.append((company, fail_result))
                self._save_result(company.ticker, run_id, fail_result)

        passed = sum(1 for _, r in results if r.passed)
        logger.info(f"[F{self.filter_num}] Complete: {passed}/{len(results)} passed")
        return results

    def _save_result(self, ticker: str, run_id: str, result: FilterResult) -> None:
        """Save filter result to database."""
        prefix = f"f{self.filter_num}"
        kwargs = {
            f"{prefix}_passed": result.passed,
        }

        if result.reasoning:
            kwargs[f"{prefix}_reason" if self.filter_num == 1 else f"{prefix}_reasoning"] = result.reasoning

        if result.score is not None:
            kwargs[f"{prefix}_score"] = result.score

        # Add any extra details
        for key, value in result.details.items():
            col_name = f"{prefix}_{key}"
            kwargs[col_name] = value

        self.db.save_analysis_result(ticker, run_id, **kwargs)
