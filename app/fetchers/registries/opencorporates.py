import logging
from datetime import datetime, timezone
from typing import Optional
import httpx

from app.config import get_settings
from app.fetchers.base import BaseFetcher, ExtractedEvidence, RawEvidenceBag
from app.models.provenance import SourceType

logger = logging.getLogger(__name__)


class OpenCorporatesFetcher(BaseFetcher):
    """
    Tier B: Government Registry (OpenCorporates)
    Pluggable adapter. If OPENCORPORATES_API_KEY is configured, queries OpenCorporates API.
    If absent, gracefully skips without impacting the pipeline.
    """

    BASE_URL = "https://api.opencorporates.com/v0.4/companies/search"

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.client = client

    async def fetch(
        self,
        canonical_domain: str,
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        if evidence_bag is None:
            evidence_bag = RawEvidenceBag(canonical_domain=canonical_domain, brand_name=brand_name)

        api_key = self.settings.OPENCORPORATES_API_KEY
        if not api_key:
            logger.debug("OpenCorporates API key not configured; skipping Tier B commercial registry call.")
            return evidence_bag

        query = brand_name or canonical_domain.split(".")[0]
        client_to_close = None
        client = self.client

        if client is None:
            client_to_close = httpx.AsyncClient(timeout=self.settings.HTTP_TIMEOUT_SECONDS)
            client = client_to_close

        try:
            params = {"q": query, "api_token": api_key}
            resp = await client.get(self.BASE_URL, params=params)
            if resp.status_code == 200:
                data = resp.json()
                companies = data.get("results", {}).get("companies", [])
                if companies:
                    top = companies[0].get("company", {})
                    now_iso = datetime.now(timezone.utc).isoformat()
                    evidence_bag.registry_filings.append(top)

                    comp_name = top.get("name")
                    if comp_name:
                        evidence_bag.extracted_evidence.append(
                            ExtractedEvidence(
                                field="legal_name",
                                value=comp_name.title(),
                                source_name=f"OpenCorporates ({top.get('jurisdiction_code', '').upper()})",
                                source_type=SourceType.GOVERNMENT_REGISTRY.value,
                                confidence=1.0,
                                filing_id=top.get("company_number"),
                                retrieved_at=now_iso
                            )
                        )
                    inc_date = top.get("incorporation_date")
                    if inc_date:
                        try:
                            year = int(inc_date.split("-")[0])
                            evidence_bag.extracted_evidence.append(
                                ExtractedEvidence(
                                    field="founded_year",
                                    value=year,
                                    source_name="OpenCorporates Registry",
                                    source_type=SourceType.GOVERNMENT_REGISTRY.value,
                                    confidence=1.0,
                                    filing_id=top.get("company_number"),
                                    retrieved_at=now_iso
                                )
                            )
                        except ValueError:
                            pass
        except Exception as e:
            logger.warning(f"OpenCorporates lookup failed: {e}")
        finally:
            if client_to_close:
                await client_to_close.aclose()

        return evidence_bag
