from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

from app.models.company import CompanyData
from app.models.provenance import Discrepancy, FieldProvenance


class ConfidenceTier(str, Enum):
    HIGH_ASSURANCE = "HIGH_ASSURANCE"          # >= 0.95: Verified across official registry or first-party legal terms
    MODERATE_ASSURANCE = "MODERATE_ASSURANCE"  # 0.70 - 0.94: Credible primary source without conflicts
    LOW_ASSURANCE = "LOW_ASSURANCE"            # < 0.70: Conflicting or sparse data
    DISAMBIGUATION_REQUIRED = "DISAMBIGUATION_REQUIRED" # Multiple matching entities found


class CandidateEntity(BaseModel):
    name: str
    domain: Optional[str] = None
    description: Optional[str] = None
    jurisdiction: Optional[str] = None
    score: float = 0.0


class EnrichmentResponse(BaseModel):
    status: str = Field(..., description="'success', 'needs_disambiguation', or 'error'")
    request_id: str = Field(..., description="Unique UUID assigned to this enrichment run")
    canonical_domain: Optional[str] = Field(default=None, description="Resolved canonical root domain")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Overall aggregate verification score")
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
        description="Suggested candidate entities if disambiguation is required"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Performance metadata (latency, cache hit, execution timestamp)"
    )

