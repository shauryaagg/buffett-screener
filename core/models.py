from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class CompanyInfo(BaseModel):
    ticker: str
    name: str
    sic: Optional[int] = None
    industry: Optional[str] = None
    market_cap: Optional[float] = None
    price: Optional[float] = None
    exchange: Optional[str] = None


class FilterResult(BaseModel):
    passed: bool
    score: Optional[float] = None
    reasoning: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)


class ManagementQualityScore(BaseModel):
    business_clarity: float = Field(ge=0, le=10)
    risk_honesty: float = Field(ge=0, le=10)
    mda_transparency: float = Field(ge=0, le=10)
    kpi_quality: float = Field(ge=0, le=10)
    tone_authenticity: float = Field(ge=0, le=10)

    @property
    def weighted_score(self) -> float:
        """MD&A 50%, Business 25%, Risk 25%. Scale to 0-100."""
        mda_avg = (self.mda_transparency + self.kpi_quality + self.tone_authenticity) / 3
        business = self.business_clarity
        risk = self.risk_honesty
        return (mda_avg * 0.50 + business * 0.25 + risk * 0.25) * 10


class ValuationResult(BaseModel):
    normalized_earnings: Optional[float] = None
    moat_type: Optional[str] = None
    moat_strength: Optional[float] = Field(default=None, ge=0, le=10)
    earning_power_multiple: Optional[float] = None
    intrinsic_value: Optional[float] = None
    current_price: Optional[float] = None
    margin_of_safety: Optional[float] = None
    reasoning: str = ""


class CapitalAllocationScore(BaseModel):
    buyback_quality: float = Field(ge=0, le=10)
    capital_return: float = Field(ge=0, le=10)
    acquisition_quality: float = Field(ge=0, le=10)
    debt_management: float = Field(ge=0, le=10)
    reinvestment_quality: float = Field(ge=0, le=10)

    @property
    def weighted_score(self) -> float:
        """Equal weighting, scale to 0-100."""
        total = (
            self.buyback_quality
            + self.capital_return
            + self.acquisition_quality
            + self.debt_management
            + self.reinvestment_quality
        )
        return total / 5 * 10


class PipelineStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineState(BaseModel):
    run_id: str
    current_filter: int = 1
    current_ticker_idx: int = 0
    started_at: Optional[datetime] = None
    paused_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: PipelineStatus = PipelineStatus.RUNNING
    ticker_limit: Optional[int] = None


class FullAnalysis(BaseModel):
    company: CompanyInfo
    f1_result: Optional[FilterResult] = None
    f2_result: Optional[FilterResult] = None
    f2_scores: Optional[ManagementQualityScore] = None
    f3_result: Optional[FilterResult] = None
    f3_valuation: Optional[ValuationResult] = None
    f4_result: Optional[FilterResult] = None
    f4_scores: Optional[CapitalAllocationScore] = None
    final_passed: bool = False
    analyzed_at: Optional[datetime] = None
