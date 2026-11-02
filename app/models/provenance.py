from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class SourceType(str, Enum):
    GOVERNMENT_REGISTRY = "GOVERNMENT_REGISTRY"
    FIRST_PARTY_TERMS = "FIRST_PARTY_TERMS"
    FIRST_PARTY_JSONLD = "FIRST_PARTY_JSONLD"
    FIRST_PARTY_ABOUT = "FIRST_PARTY_ABOUT"
    FIRST_PARTY_CONTACT = "FIRST_PARTY_CONTACT"
    FIRST_PARTY_HOMEPAGE = "FIRST_PARTY_HOMEPAGE"
    COMMERCIAL_API = "COMMERCIAL_API"
    SEARCH_INFERENCE = "SEARCH_INFERENCE"


class FieldProvenance(BaseModel):
    source: str = Field(..., description="Name or URL of the data source providing this field")
    source_type: SourceType = Field(..., description="Categorical authority level of the provider")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Field-level confidence score (0.0 to 1.0)")
    exact_quote: Optional[str] = Field(default=None, description="Exact verbatim citation quote from ground-truth source")
    filing_id: Optional[str] = Field(default=None, description="Government registry or official filing identifier")
    verified_at: str = Field(..., description="ISO 8601 UTC timestamp of verification")


class Discrepancy(BaseModel):
    field: str = Field(..., description="Target field that experienced conflicting values")
    competing_values: list[dict[str, Any]] = Field(
        ...,
        description="List of competing values with their respective source and authority score"
    )
    resolution_rationale: str = Field(..., description="Auditable reasoning for selecting the winning value")

