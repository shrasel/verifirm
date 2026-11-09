from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class SourceType(str, Enum):
    GOVERNMENT_REGISTRY = "GOVERNMENT_REGISTRY"        # Official statutory registry (SEC EDGAR, UK Companies House, GLEIF LEI)
    REGISTRY_AGGREGATOR = "REGISTRY_AGGREGATOR"        # Aggregated registry index (OpenCorporates)
    PUBLIC_KNOWLEDGE_GRAPH = "PUBLIC_KNOWLEDGE_GRAPH"  # Public knowledge graph (Wikidata)
    FIRST_PARTY_TERMS = "FIRST_PARTY_TERMS"            # Website /terms, /privacy, legal notice
    FIRST_PARTY_JSONLD = "FIRST_PARTY_JSONLD"          # Website embedded Schema.org JSON-LD
    FIRST_PARTY_ABOUT = "FIRST_PARTY_ABOUT"            # Website /about page
    FIRST_PARTY_CONTACT = "FIRST_PARTY_CONTACT"        # Website /contact page
    FIRST_PARTY_HOMEPAGE = "FIRST_PARTY_HOMEPAGE"      # Website root landing page
    COMMERCIAL_API = "COMMERCIAL_API"                  # Third-party B2B vendor (PDL, D&B, etc.)
    SEARCH_INFERENCE = "SEARCH_INFERENCE"              # Web search snippet / discovery signal


class SourceFamily(str, Enum):
    """
    Groups SourceTypes by true evidentiary independence.
    Multiple claims within the same family (e.g., JSON-LD on '/' and text on '/terms'
    from the same domain) are first-party self-assertions, NOT independent cross-verification.
    """
    OFFICIAL_REGISTRY = "OFFICIAL_REGISTRY"
    REGISTRY_AGGREGATOR = "REGISTRY_AGGREGATOR"
    PUBLIC_KNOWLEDGE_GRAPH = "PUBLIC_KNOWLEDGE_GRAPH"
    FIRST_PARTY_WEB = "FIRST_PARTY_WEB"
    COMMERCIAL_VENDOR = "COMMERCIAL_VENDOR"
    SEARCH_INDEX = "SEARCH_INDEX"


SOURCE_TYPE_TO_FAMILY: dict[str, SourceFamily] = {
    SourceType.GOVERNMENT_REGISTRY.value: SourceFamily.OFFICIAL_REGISTRY,
    SourceType.REGISTRY_AGGREGATOR.value: SourceFamily.REGISTRY_AGGREGATOR,
    SourceType.PUBLIC_KNOWLEDGE_GRAPH.value: SourceFamily.PUBLIC_KNOWLEDGE_GRAPH,
    SourceType.FIRST_PARTY_TERMS.value: SourceFamily.FIRST_PARTY_WEB,
    SourceType.FIRST_PARTY_JSONLD.value: SourceFamily.FIRST_PARTY_WEB,
    SourceType.FIRST_PARTY_ABOUT.value: SourceFamily.FIRST_PARTY_WEB,
    SourceType.FIRST_PARTY_CONTACT.value: SourceFamily.FIRST_PARTY_WEB,
    SourceType.FIRST_PARTY_HOMEPAGE.value: SourceFamily.FIRST_PARTY_WEB,
    SourceType.COMMERCIAL_API.value: SourceFamily.COMMERCIAL_VENDOR,
    SourceType.SEARCH_INFERENCE.value: SourceFamily.SEARCH_INDEX,
}


def get_source_family(source_type: str) -> SourceFamily:
    return SOURCE_TYPE_TO_FAMILY.get(source_type, SourceFamily.SEARCH_INDEX)


class FieldProvenance(BaseModel):
    source: str = Field(..., description="Name or URL of the data source providing this field")
    source_type: SourceType = Field(..., description="Categorical authority level of the provider")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Calibrated field-level confidence score (0.0 to 1.0)")
    exact_quote: Optional[str] = Field(default=None, description="Exact verbatim citation quote from ground-truth source")
    filing_id: Optional[str] = Field(default=None, description="Government registry or official filing identifier")
    verified_at: str = Field(..., description="ISO 8601 UTC timestamp of retrieval/verification")
    source_updated_at: Optional[str] = Field(default=None, description="Source record update timestamp if known")
    raw_claim: Optional[Any] = Field(default=None, description="Raw untransformed value as asserted by source")
    transformation_applied: Optional[str] = Field(default=None, description="Normalization or transformation applied")
    independent_sources_count: int = Field(default=1, description="Number of independent source families agreeing on this value")


class Discrepancy(BaseModel):
    field: str = Field(..., description="Target field that experienced conflicting values")
    competing_values: list[dict[str, Any]] = Field(
        ...,
        description="List of competing values with their respective source and authority score"
    )
    resolution_rationale: str = Field(..., description="Auditable reasoning for selecting the winning value")
