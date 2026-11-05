import logging
from typing import Optional
import httpx

from app.consensus.confidence_scorer import ConsensusAndScoringEngine
from app.fetchers.base import RawEvidenceBag
from app.fetchers.jsonld_extractor import JsonLdExtractor
from app.fetchers.registries.sec_edgar import SecEdgarFetcher
from app.fetchers.web_crawler import FirstPartyWebCrawler
from app.models.request import EnrichmentRequest
from app.pipeline.stage1_canonical import CanonicalIdentityResolver, CanonicalResolutionResult
from app.pipeline.stage4_grounding import GroundingAuditor

logger = logging.getLogger(__name__)


class AgentTools:
    """
    Modular, single-responsibility tools exposed to the Company Enrichment Agent.
    Each tool returns auditable results and updates the shared evidence bag.
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.client = client
        self.canonical_resolver = CanonicalIdentityResolver(client=client)
        self.web_crawler = FirstPartyWebCrawler(client=client)
        self.sec_fetcher = SecEdgarFetcher(client=client)
        self.consensus_engine = ConsensusAndScoringEngine()
        self.grounding_auditor = GroundingAuditor()

    async def resolve_identity(
        self,
        name: Optional[str] = None,
        website: Optional[str] = None,
        jurisdiction_hint: Optional[str] = None
    ) -> CanonicalResolutionResult:
        """
        Resolves domain redirects, cleans subdomains, or triggers search fallback.
        """
        req = EnrichmentRequest(
            name=name,
            website=website,
            jurisdiction_hint=jurisdiction_hint
        )
        return await self.canonical_resolver.resolve(req)

    async def crawl_first_party_web(
        self,
        canonical_domain: str,
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        """
        Crawls key first-party paths (/, /about, /terms, /privacy, /contact)
        extracting textual corpus, footer copyright, and contact details.
        """
        return await self.web_crawler.fetch(canonical_domain, brand_name, evidence_bag)

    def extract_structured_jsonld(self, evidence_bag: RawEvidenceBag) -> int:
        """
        Extracts Schema.org Corporation / Organization structured data from crawled HTML pages.
        Returns the number of structured claims discovered.
        """
        before_count = len(evidence_bag.extracted_evidence)
        for page in evidence_bag.pages:
            JsonLdExtractor.extract_from_html(page.raw_html, page.url, evidence_bag)
        return len(evidence_bag.extracted_evidence) - before_count

    async def query_government_registry(
        self,
        canonical_domain: str,
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        """
        Queries official US SEC EDGAR filings for CIK, legal registered name, and SIC code.
        """
        return await self.sec_fetcher.fetch(canonical_domain, brand_name, evidence_bag)

    def audit_grounding(
        self,
        evidence_bag: RawEvidenceBag,
        provenance: dict
    ) -> list[str]:
        """
        Audits extracted textual quotes against raw scraped corpus to eliminate hallucinations.
        Returns list of fields that failed grounding audit.
        """
        from app.models.company import CompanyData
        dummy_data = CompanyData()
        return self.grounding_auditor.audit_provenance(
            data=dummy_data,
            provenance=provenance,
            raw_corpus=evidence_bag.full_text_corpus
        )

    def reconcile_consensus(
        self,
        evidence_bag: RawEvidenceBag,
        active_web_presence: bool = True
    ) -> tuple:
        """
        Applies Authority Matrix weighting, resolves conflicting claims,
        and computes field-level and composite confidence scores.
        """
        return self.consensus_engine.reconcile_all(
            evidence_bag=evidence_bag,
            active_web_presence=active_web_presence
        )
