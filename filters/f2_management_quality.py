"""Filter 2: Management Quality — multi-agent 10-K qualitative analysis."""
import logging
from filters.filter_base import FilterBase, _is_rate_limit
from core.models import CompanyInfo, FilterResult, ManagementQualityScore
from core.database import Database
from data.edgar_client import EdgarClient
from config.settings import F2_MIN_SCORE
from agents.definitions import (
    analyze_business_description,
    analyze_risk_factors,
    analyze_mda,
)

logger = logging.getLogger(__name__)


class ManagementQualityFilter(FilterBase):
    filter_num = 2
    filter_name = "management_quality"

    def __init__(self, db: Database, edgar: EdgarClient):
        super().__init__(db)
        self.edgar = edgar

    async def evaluate(self, company: CompanyInfo) -> FilterResult:
        """
        Multi-agent analysis of 10-K sections:
        1. Extract Item 1, Item 1A, Item 7 from latest 10-K
        2. Run Business Analyst on Item 1 (Sonnet)
        3. If Item 1 business_clarity < 3, early exit (skip expensive Opus MD&A call)
        4. Run Risk Analyst on Item 1A (Sonnet) in parallel with MD&A Analyst on Item 7 (Opus)
        5. Synthesize scores into ManagementQualityScore
        6. Threshold: weighted score >= F2_MIN_SCORE (65) to pass
        """
        # Step 1: Get 10-K sections
        sections = self.edgar.get_tenk_sections(company.ticker)
        if not sections:
            return FilterResult(
                passed=False,
                reasoning="Could not extract 10-K sections"
            )

        item1 = sections.get("item_1", "")
        item1a = sections.get("item_1a", "")
        item7 = sections.get("item_7", "")

        if not item1 and not item7:
            return FilterResult(
                passed=False,
                reasoning="Neither Item 1 nor Item 7 could be extracted from 10-K"
            )

        # Step 2: Analyze Item 1 (Business Description) — Sonnet
        business_result = {}
        if item1:
            business_result = await analyze_business_description(item1[:50000])

        # Step 3: Early exit if business clarity is very low
        try:
            business_clarity = float(business_result.get("business_clarity", 5)) if business_result.get("business_clarity") is not None else 5.0
        except (TypeError, ValueError):
            business_clarity = 5.0
        if business_clarity < 3 and "error" not in business_result:
            return FilterResult(
                passed=False,
                score=business_clarity * 10,
                reasoning=f"Early exit: business clarity score {business_clarity}/10 below threshold. {business_result.get('reasoning', '')}",
                details={
                    "business_clarity": business_clarity,
                }
            )

        # Step 4: Run Risk Analyst and MD&A Analyst sequentially
        # (claude-code-sdk spawns subprocesses; concurrent calls cause async context issues)
        risk_result = {}
        mda_result = {}

        if item1a:
            try:
                risk_result = await analyze_risk_factors(item1a[:50000])
            except Exception as e:
                if _is_rate_limit(e):
                    raise
                logger.warning(f"  {company.ticker}: risk analysis failed: {e}")

        if item7:
            try:
                mda_result = await analyze_mda(item7[:80000])
            except Exception as e:
                if _is_rate_limit(e):
                    raise
                logger.warning(f"  {company.ticker}: MD&A analysis failed: {e}")

        # Step 5: Synthesize scores — incorporate all available dimensions
        def sf(val, default=5.0):
            """Safe float conversion — handles None, non-numeric values."""
            try:
                return float(val) if val is not None else default
            except (TypeError, ValueError):
                return default

        # Business agent: average all sub-scores
        moat_articulation = sf(business_result.get("moat_articulation", 5))
        honest_self = sf(business_result.get("honest_self_assessment", 5))
        business_clarity = sf(business_clarity)
        business_avg = (business_clarity + moat_articulation + honest_self) / 3

        # Risk agent: average all sub-scores
        risk_honesty_raw = sf(risk_result.get("risk_honesty", 5))
        risk_specificity = sf(risk_result.get("specificity", 5))
        risk_quantification = sf(risk_result.get("quantification", 5))
        risk_avg = (risk_honesty_raw + risk_specificity + risk_quantification) / 3

        # MDA agent: average all sub-scores
        kpi_quality = sf(mda_result.get("kpi_quality", 5))
        mda_transparency = sf(mda_result.get("transparency", 5))
        explanation_quality = sf(mda_result.get("explanation_quality", 5))
        capital_discussion = sf(mda_result.get("capital_allocation_discussion", 5))
        forward_honesty = sf(mda_result.get("forward_looking_honesty", 5))
        tone_authenticity = (explanation_quality + forward_honesty) / 2

        def clamp(v): return max(0.0, min(10.0, v))

        scores = ManagementQualityScore(
            business_clarity=clamp(business_avg),
            risk_honesty=clamp(risk_avg),
            mda_transparency=clamp(mda_transparency),
            kpi_quality=clamp((kpi_quality + capital_discussion) / 2),
            tone_authenticity=clamp(tone_authenticity),
        )

        weighted = scores.weighted_score
        passed = weighted >= F2_MIN_SCORE

        # Build reasoning
        reasoning_parts = []
        if business_result.get("reasoning"):
            reasoning_parts.append(f"Business: {business_result['reasoning'][:500]}")
        if risk_result.get("reasoning"):
            reasoning_parts.append(f"Risk: {risk_result['reasoning'][:500]}")
        if mda_result.get("reasoning"):
            reasoning_parts.append(f"MD&A: {mda_result['reasoning'][:500]}")

        full_reasoning = " | ".join(reasoning_parts)

        return FilterResult(
            passed=passed,
            score=weighted,
            reasoning=full_reasoning,
            details={
                "business_clarity": scores.business_clarity,
                "risk_honesty": scores.risk_honesty,
                "mda_transparency": scores.mda_transparency,
                "kpi_quality": scores.kpi_quality,
                "tone": scores.tone_authenticity,
            }
        )
