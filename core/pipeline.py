"""Main pipeline orchestrator — sequential filter execution with pause/resume."""
import asyncio
import uuid
import logging
from datetime import datetime
from typing import Optional

from core.models import (
    CompanyInfo, PipelineState, PipelineStatus, FullAnalysis,
    FilterResult, ManagementQualityScore, ValuationResult, CapitalAllocationScore,
)
from core.database import Database
from data.edgar_client import EdgarClient
from data.financial_data import FinancialDataService
from config.settings import RATE_LIMIT_PAUSE_HOURS
from filters.f1_business_type import BusinessTypeFilter
from filters.f2_management_quality import ManagementQualityFilter
from filters.f3_valuation import ValuationFilter
from filters.f4_capital_allocation import CapitalAllocationFilter

logger = logging.getLogger(__name__)


def _try_create_market_data():
    """Create MarketDataService. Uses Yahoo Finance — no API key needed."""
    try:
        from data.market_data import MarketDataService
        return MarketDataService()
    except Exception as e:
        logger.warning(f"MarketDataService unavailable: {e}")
        return None


class Pipeline:
    def __init__(self, db: Database):
        self.db = db
        self.edgar = EdgarClient(db)
        self.market_data = _try_create_market_data()
        self.financial_data = FinancialDataService()

        self.filters = [
            BusinessTypeFilter(db, self.edgar, self.market_data),
            ManagementQualityFilter(db, self.edgar),
            ValuationFilter(db, self.financial_data, self.edgar, self.market_data),
            CapitalAllocationFilter(db, self.financial_data, self.edgar),
        ]

    async def run(self, run_id: str = None, limit: int = None) -> str:
        """
        Run the full pipeline.
        1. Build universe via Filter 1's build_universe()
        2. Optionally limit universe size
        3. Run each filter sequentially — only companies that passed the previous filter
        4. On rate limit: save state, sleep, auto-resume
        5. Mark complete when done
        """
        if self.market_data is None:
            raise RuntimeError("MarketDataService could not be initialized.")

        run_id = run_id or str(uuid.uuid4())[:8]
        logger.info(f"Starting pipeline run: {run_id}")

        universe = self.filters[0].build_universe()
        if limit:
            universe = universe[:limit]

        logger.info(f"Universe: {len(universe)} companies")

        state = PipelineState(
            run_id=run_id,
            current_filter=1,
            current_ticker_idx=0,
            started_at=datetime.now(),
            status=PipelineStatus.RUNNING,
            ticker_limit=limit,
        )
        self.db.save_pipeline_state(state)

        current_companies = universe

        for filter_idx, filt in enumerate(self.filters):
            filter_num = filter_idx + 1
            logger.info(f"\n{'='*60}")
            logger.info(f"FILTER {filter_num}: {filt.filter_name} — {len(current_companies)} companies")
            logger.info(f"{'='*60}")

            self.db.update_pipeline_status(
                run_id, PipelineStatus.RUNNING,
                current_filter=filter_num, current_ticker_idx=0
            )

            try:
                results = await filt.run_batch(current_companies, run_id)
                passed_companies = [company for company, result in results if result.passed]
                logger.info(f"Filter {filter_num}: {len(passed_companies)}/{len(current_companies)} passed")

                if not passed_companies:
                    logger.info("No companies passed this filter. Pipeline complete.")
                    break

                current_companies = passed_companies

            except Exception as e:
                if _is_rate_limit_error(e):
                    logger.warning(f"\nRate limit hit during Filter {filter_num}.")
                    logger.warning(f"Pausing for {RATE_LIMIT_PAUSE_HOURS}h. Will auto-resume.")
                    self.db.update_pipeline_status(
                        run_id, PipelineStatus.PAUSED,
                        paused_at=datetime.now(),
                        current_filter=filter_num,
                    )
                    await asyncio.sleep(RATE_LIMIT_PAUSE_HOURS * 3600)
                    logger.info("Resuming after rate limit pause...")
                    return await self.resume(run_id)
                else:
                    logger.error(f"Pipeline error in Filter {filter_num}: {e}")
                    self.db.update_pipeline_status(run_id, PipelineStatus.FAILED)
                    raise

        for company in current_companies:
            self.db.save_analysis_result(
                company.ticker, run_id,
                final_passed=True,
                analyzed_at=datetime.now().isoformat()
            )

        self.db.update_pipeline_status(
            run_id, PipelineStatus.COMPLETED,
            completed_at=datetime.now()
        )

        _log_run_summary(self.db, run_id)
        return run_id

    async def resume(self, run_id: str) -> str:
        """Resume a paused or failed pipeline run."""
        state = self.db.load_pipeline_state(run_id)
        if not state:
            raise ValueError(f"No pipeline state found for run_id: {run_id}")

        if state.status == PipelineStatus.COMPLETED:
            logger.info(f"Run {run_id} already completed.")
            return run_id

        logger.info(f"Resuming run {run_id} from Filter {state.current_filter}, "
                     f"ticker index {state.current_ticker_idx}")

        self.db.update_pipeline_status(run_id, PipelineStatus.RUNNING)

        # Rebuild company list for current position
        if state.current_filter == 1:
            current_companies = self.filters[0].build_universe()
            if state.ticker_limit:
                current_companies = current_companies[:state.ticker_limit]
        else:
            prev_tickers = self.db.get_passed_tickers(run_id, state.current_filter - 1)
            current_companies = self._rebuild_companies(prev_tickers)

        for filter_idx in range(state.current_filter - 1, len(self.filters)):
            filt = self.filters[filter_idx]
            filter_num = filter_idx + 1
            start_idx = state.current_ticker_idx if filter_num == state.current_filter else 0

            logger.info(f"\nFilter {filter_num}: {filt.filter_name} — "
                        f"{len(current_companies)} companies (starting at idx {start_idx})")

            self.db.update_pipeline_status(
                run_id, PipelineStatus.RUNNING,
                current_filter=filter_num, current_ticker_idx=start_idx
            )

            try:
                results = await filt.run_batch(current_companies, run_id, start_idx=start_idx)
                passed_companies = [c for c, r in results if r.passed]

                # Include companies before start_idx that already passed this filter
                if start_idx > 0:
                    prev_passed = set(self.db.get_passed_tickers(run_id, filter_num))
                    already_in = {c.ticker for c in passed_companies}
                    for c in current_companies[:start_idx]:
                        if c.ticker in prev_passed and c.ticker not in already_in:
                            passed_companies.append(c)

                if not passed_companies:
                    break

                current_companies = passed_companies

            except Exception as e:
                if _is_rate_limit_error(e):
                    logger.warning(f"Rate limit hit. Pausing for {RATE_LIMIT_PAUSE_HOURS}h.")
                    self.db.update_pipeline_status(
                        run_id, PipelineStatus.PAUSED,
                        paused_at=datetime.now(),
                    )
                    await asyncio.sleep(RATE_LIMIT_PAUSE_HOURS * 3600)
                    return await self.resume(run_id)
                else:
                    self.db.update_pipeline_status(run_id, PipelineStatus.FAILED)
                    raise

        for company in current_companies:
            self.db.save_analysis_result(
                company.ticker, run_id,
                final_passed=True,
                analyzed_at=datetime.now().isoformat()
            )

        self.db.update_pipeline_status(
            run_id, PipelineStatus.COMPLETED,
            completed_at=datetime.now()
        )

        return run_id

    async def run_single(self, ticker: str, verbose: bool = False) -> FullAnalysis:
        """Run all 4 filters on a single ticker. Returns FullAnalysis."""
        if verbose:
            logger.setLevel(logging.DEBUG)

        company = self._build_single_company(ticker)
        run_id = f"single_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        analysis = FullAnalysis(company=company, analyzed_at=datetime.now())

        logger.info(f"\n{'='*60}")
        logger.info(f"ANALYZING: {ticker} ({company.name})")
        if company.market_cap:
            logger.info(f"Price: ${company.price or 'N/A'} | Market Cap: ${company.market_cap/1e6:.1f}M")
        else:
            logger.info(f"Price: ${company.price or 'N/A'}")
        logger.info(f"{'='*60}")

        # Filter 1: Business Type
        logger.info("\n--- Filter 1: Business Type ---")
        f1_result = await self.filters[0].evaluate(company)
        analysis.f1_result = f1_result
        logger.info(f"  Result: {'PASS' if f1_result.passed else 'FAIL'}")
        logger.info(f"  Reason: {f1_result.reasoning}")

        if not f1_result.passed:
            analysis.final_passed = False
            return analysis

        # Filter 2: Management Quality
        logger.info("\n--- Filter 2: Management Quality ---")
        f2_result = await self.filters[1].evaluate(company)
        analysis.f2_result = f2_result
        if f2_result.details:
            try:
                analysis.f2_scores = ManagementQualityScore(
                    business_clarity=f2_result.details.get("business_clarity", 5),
                    risk_honesty=f2_result.details.get("risk_honesty", 5),
                    mda_transparency=f2_result.details.get("mda_transparency", 5),
                    kpi_quality=f2_result.details.get("kpi_quality", 5),
                    tone_authenticity=f2_result.details.get("tone", 5),
                )
            except Exception:
                pass
        score_str = f" (score: {f2_result.score:.1f})" if f2_result.score is not None else ""
        logger.info(f"  Result: {'PASS' if f2_result.passed else 'FAIL'}{score_str}")
        logger.info(f"  Reason: {f2_result.reasoning[:200]}")

        if not f2_result.passed:
            analysis.final_passed = False
            return analysis

        # Filter 3: Valuation
        logger.info("\n--- Filter 3: Valuation ---")
        f3_result = await self.filters[2].evaluate(company)
        analysis.f3_result = f3_result
        if f3_result.details:
            try:
                analysis.f3_valuation = ValuationResult(
                    normalized_earnings=f3_result.details.get("normalized_earnings"),
                    moat_type=f3_result.details.get("moat_type"),
                    moat_strength=f3_result.details.get("moat_strength"),
                    earning_power_multiple=f3_result.details.get("earning_power_multiple"),
                    intrinsic_value=f3_result.details.get("intrinsic_value"),
                    current_price=f3_result.details.get("current_price"),
                    margin_of_safety=f3_result.details.get("margin_of_safety"),
                    reasoning=f3_result.reasoning[:2000],
                )
            except Exception:
                pass
        logger.info(f"  Result: {'PASS' if f3_result.passed else 'FAIL'}")
        if f3_result.details:
            iv = f3_result.details.get("intrinsic_value")
            mos = f3_result.details.get("margin_of_safety")
            if iv:
                logger.info(f"  Intrinsic Value: ${iv:.2f}")
            if mos is not None:
                logger.info(f"  Margin of Safety: {mos:.1%}")

        if not f3_result.passed:
            analysis.final_passed = False
            return analysis

        # Filter 4: Capital Allocation
        logger.info("\n--- Filter 4: Capital Allocation ---")
        f4_result = await self.filters[3].evaluate(company)
        analysis.f4_result = f4_result
        if f4_result.details:
            try:
                analysis.f4_scores = CapitalAllocationScore(
                    buyback_quality=f4_result.details.get("buyback_quality", 5),
                    capital_return=f4_result.details.get("capital_return", 5),
                    acquisition_quality=f4_result.details.get("acquisition_quality", 5),
                    debt_management=f4_result.details.get("debt_management", 5),
                    reinvestment_quality=f4_result.details.get("reinvestment_quality", 5),
                )
            except Exception:
                pass
        score_str = f" (score: {f4_result.score:.1f})" if f4_result.score is not None else ""
        logger.info(f"  Result: {'PASS' if f4_result.passed else 'FAIL'}{score_str}")

        analysis.final_passed = f4_result.passed

        if analysis.final_passed:
            logger.info(f"\n*** {ticker} PASSED ALL FILTERS ***")

        return analysis

    def get_status(self, run_id: str = None) -> dict:
        """Get status of a pipeline run, or latest run if no run_id."""
        if not run_id:
            return {"error": "No run_id provided"}

        state = self.db.load_pipeline_state(run_id)
        if not state:
            return {"error": f"Run {run_id} not found"}

        summary = self.db.get_run_summary(run_id)
        return {
            "run_id": state.run_id,
            "status": state.status.value,
            "current_filter": state.current_filter,
            "current_ticker_idx": state.current_ticker_idx,
            "started_at": state.started_at.isoformat() if state.started_at else None,
            "summary": summary,
        }

    def _build_single_company(self, ticker: str) -> CompanyInfo:
        """Build CompanyInfo for a single ticker, using market data if available."""
        if self.market_data:
            quote = self.market_data.get_single_quote(ticker)
            if quote:
                return CompanyInfo(
                    ticker=ticker,
                    name=quote.get("name", ticker),
                    market_cap=quote.get("market_cap"),
                    price=quote.get("price"),
                    exchange=quote.get("exchange"),
                )
        return CompanyInfo(ticker=ticker, name=ticker)

    def _rebuild_companies(self, tickers: list) -> list:
        """Rebuild CompanyInfo objects from tickers, using market data if available."""
        if self.market_data:
            all_prices = self.market_data.fetch_all_prices()
        else:
            all_prices = {}

        companies = []
        for ticker in tickers:
            data = all_prices.get(ticker, {})
            companies.append(CompanyInfo(
                ticker=ticker,
                name=data.get("name", ticker),
                market_cap=data.get("market_cap"),
                price=data.get("price"),
                exchange=data.get("exchange"),
            ))
        return companies


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check for real rate limit errors, not SDK parse failures."""
    if "ParseError" in type(exc).__name__:
        return False
    error_str = str(exc).lower()
    return any(term in error_str for term in ("429", "overloaded", "rate limit exceeded", "too many requests"))


def _log_run_summary(db: Database, run_id: str):
    summary = db.get_run_summary(run_id)
    logger.info(f"\n{'='*60}")
    logger.info(f"PIPELINE COMPLETE: {run_id}")
    logger.info(f"  Total analyzed: {summary.get('total', 0)}")
    logger.info(f"  F1 passed: {summary.get('f1_passed', 0)}")
    logger.info(f"  F2 passed: {summary.get('f2_passed', 0)}")
    logger.info(f"  F3 passed: {summary.get('f3_passed', 0)}")
    logger.info(f"  F4 passed: {summary.get('f4_passed', 0)}")
    logger.info(f"  Final passed: {summary.get('final_passed', 0)}")
    logger.info(f"{'='*60}")
