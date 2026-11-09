from abc import ABC, abstractmethod
from typing import Any, Optional
from pydantic import BaseModel, Field


class ScrapedPage(BaseModel):
    path: str
    url: str
    status_code: int
    raw_html: str
    clean_text: str
    title: Optional[str] = None
    meta_description: Optional[str] = None
    copyright_notice: Optional[str] = None


class ExtractedEvidence(BaseModel):
    field: str
    value: Any
    source_name: str
    source_type: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    verbatim_quote: Optional[str] = None
    filing_id: Optional[str] = None
    retrieved_at: str
    source_url: Optional[str] = None
    source_updated_at: Optional[str] = None
    raw_claim: Optional[Any] = None
    transformation_applied: Optional[str] = None


class RawEvidenceBag(BaseModel):
    canonical_domain: Optional[str] = None
    brand_name: Optional[str] = None
    jurisdiction_hint: Optional[str] = None
    cik: Optional[str] = None
    lei: Optional[str] = None
    registration_number: Optional[str] = None
    ticker: Optional[str] = None
    exchange: Optional[str] = None
    http_status: Optional[int] = None
    pages: list[ScrapedPage] = Field(default_factory=list)
    jsonld_entities: list[dict[str, Any]] = Field(default_factory=list)
    registry_filings: list[dict[str, Any]] = Field(default_factory=list)
    commercial_records: list[dict[str, Any]] = Field(default_factory=list)
    extracted_evidence: list[ExtractedEvidence] = Field(default_factory=list)
    full_text_corpus: str = ""

    def add_page(self, page: ScrapedPage):
        self.pages.append(page)
        if page.clean_text:
            self.full_text_corpus += f"\n--- PAGE: {page.path} ---\n{page.clean_text}\n"


class BaseFetcher(ABC):
    @abstractmethod
    async def fetch(
        self,
        canonical_domain: Optional[str],
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        """
        Asynchronously fetches and populates raw evidence for a given domain/brand/identifier.
        """
        pass
