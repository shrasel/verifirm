from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

from app.models.company import CompanyData
from app.models.provenance import Discrepancy, FieldProvenance


class MatchDecision(str, Enum):
    """
    Fellegi-Sunter three-way entity resolution outcome:
    - AUTO_MATCH: Evidence crosses high calibrated threshold with clear margin.
    - NEEDS_REVIEW: Plausible competing candidates or moderate evidence requiring review.
    - UNRESOLVED: Insufficient or weak evidence; system abstains rather than guessing.
    """
    AUTO_MATCH = "AUTO_MATCH"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNRESOLVED = "UNRESOLVED"


class ConfidenceTier(str, Enum):
    HIGH_ASSURANCE = "HIGH_ASSURANCE"                  # >= 0.88: Corroborated across independent source families
    MODERATE_ASSURANCE = "MODERATE_ASSURANCE"          # 0.65 - 0.87: Credible primary source without conflicts
    LOW_ASSURANCE = "LOW_ASSURANCE"                    # < 0.65: Sparse or weakly corroborated data
    DISAMBIGUATION_REQUIRED = "DISAMBIGUATION_REQUIRED"# Competing candidate entities (NEEDS_REVIEW)
    UNRESOLVED = "UNRESOLVED"                          # Insufficient evidence to resolve entity or domain


class CandidateEntity(BaseModel):
    name: str
    legal_name: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    jurisdiction: Optional[str] = None
    registration_number: Optional[str] = None
    cik: Optional[str] = None
    lei: Optional[str] = None
    ticker: Optional[str] = None
    exchange: Optional[str] = None
    source: Optional[str] = None
    supporting_signals: list[str] = Field(default_factory=list)
    contradicting_signals: list[str] = Field(default_factory=list)
    score: float = 0.0


class EnrichmentResponse(BaseModel):
    status: str = Field(..., description="'success', 'needs_disambiguation', 'unresolved', or 'error'")
    match_decision: MatchDecision = Field(
        default=MatchDecision.AUTO_MATCH,
        description="Three-way entity resolution decision (AUTO_MATCH, NEEDS_REVIEW, UNRESOLVED)"
    )
    request_id: str = Field(..., description="Unique UUID assigned to this enrichment run")
    canonical_domain: Optional[str] = Field(default=None, description="Resolved canonical root domain")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Calibrated aggregate verification score")
    confidence_tier: ConfidenceTier = Field(..., description="Assurance tier classification")
    data: CompanyData = Field(..., description="Reconciled, normalized company fields")
    provenance: dict[str, FieldProvenance] = Field(
        default_factory=dict,
        description="Field-by-field audit trail documenting sources, quotes, and authority"
    )
    discrepancies: list[Discrepancy] = Field(
        default_factory=list,
        description="Recorded conflicts across data providers and their resolution logic"
    )
    candidates: list[CandidateEntity] = Field(
        default_factory=list,
        description="Ranked candidate entities with supporting and contradicting signals"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Performance metadata (latency, cache hit, execution timestamp)"
    )
