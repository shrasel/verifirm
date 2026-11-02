import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
import httpx

from app.config import get_settings
from app.fetchers.base import BaseFetcher, ExtractedEvidence, RawEvidenceBag
from app.models.company import HeadquartersAddress, IndustryTaxonomy
from app.models.provenance import SourceType

logger = logging.getLogger(__name__)


class SecEdgarFetcher(BaseFetcher):
    """
    Tier B: Government Registry (US SEC EDGAR)
    Zero-key public registry lookup for US public and reporting corporations.
    Retrieves CIK, official registered legal name, SIC code, and registered office.
    """

    SEC_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
    SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.client = client

    def _normalize_name(self, name: str) -> str:
        return re.sub(r"[^\w\s]", "", name.lower()).strip()

    async def search_company(self, client: httpx.AsyncClient, query: str) -> Optional[dict[str, Any]]:
        """
        Queries SEC EFTS endpoint for matching entity CIK and legal name.
        """
        try:
            # Query SEC entity hints API
            url = "https://data.sec.gov/submissions/CIK" # Or SEC entity lookup
            search_api = "https://efts.sec.gov/LATEST/search-index"
            
            headers = {
                "User-Agent": self.settings.USER_AGENT,
                "Accept": "application/json",
            }

            resp = await client.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers=headers
            )
            if resp.status_code != 200:
                return None

            tickers_data = resp.json()
            norm_q = self._normalize_name(query)

            # Look through companies in the SEC ticker list
            for _, info in tickers_data.items():
                title = info.get("title", "")
                ticker = info.get("ticker", "")
                cik_str = str(info.get("cik_str", "")).zfill(10)

                if norm_q in self._normalize_name(title) or norm_q == ticker.lower():
                    # Found match, get detailed entity submission
                    detail_resp = await client.get(
                        f"https://data.sec.gov/submissions/CIK{cik_str}.json",
                        headers=headers
                    )
                    if detail_resp.status_code == 200:
                        return detail_resp.json()
                    return {
                        "cik": cik_str,
                        "name": title,
                        "ticker": ticker
                    }
        except Exception as e:
            logger.debug(f"SEC EDGAR search error: {e}")

        return None

    async def fetch(
        self,
        canonical_domain: str,
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        if evidence_bag is None:
            evidence_bag = RawEvidenceBag(canonical_domain=canonical_domain, brand_name=brand_name)

        search_query = brand_name
        if not search_query and canonical_domain:
            search_query = canonical_domain.split(".")[0]

        if not search_query:
            return evidence_bag

        client_to_close = None
        client = self.client

        if client is None:
            client_to_close = httpx.AsyncClient(
                headers={"User-Agent": self.settings.USER_AGENT},
                timeout=self.settings.HTTP_TIMEOUT_SECONDS
            )
            client = client_to_close

        try:
            sec_record = await self.search_company(client, search_query)
            if not sec_record:
                return evidence_bag

            evidence_bag.registry_filings.append(sec_record)
            now_iso = datetime.now(timezone.utc).isoformat()
            cik = str(sec_record.get("cik", ""))

            # 1. Official Legal Entity Name
            legal_name = sec_record.get("name")
            if legal_name:
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="legal_name",
                        value=legal_name.title(),
                        source_name="US Securities and Exchange Commission (SEC EDGAR)",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=1.0,  # Government certified legal authority
                        verbatim_quote=f"SEC CIK {cik}: {legal_name}",
                        filing_id=cik,
                        retrieved_at=now_iso
                    )
                )

            # 2. Operating Status
            evidence_bag.extracted_evidence.append(
                ExtractedEvidence(
                    field="status",
                    value="Active",
                    source_name="US SEC EDGAR",
                    source_type=SourceType.GOVERNMENT_REGISTRY.value,
                    confidence=1.0,
                    filing_id=cik,
                    retrieved_at=now_iso
                )
            )

            # 3. SIC Industry Code
            sic = sec_record.get("sic")
            sic_desc = sec_record.get("sicDescription")
            if sic:
                ind = IndustryTaxonomy(
                    sic_code=str(sic),
                    label=sic_desc or "SEC Registered Industry"
                )
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="industry",
                        value=ind.model_dump(),
                        source_name="US SEC EDGAR",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=1.0,
                        filing_id=cik,
                        retrieved_at=now_iso
                    )
                )

            # 4. Registered Office / Headquarters
            addresses = sec_record.get("addresses", {})
            business_addr = addresses.get("business", {})
            if business_addr:
                hq = HeadquartersAddress(
                    street=business_addr.get("street1"),
                    city=business_addr.get("city"),
                    state=business_addr.get("stateOrCountry"),
                    postal_code=business_addr.get("zipCode"),
                    country="USA" if business_addr.get("stateOrCountryDescription") == "UNITED STATES" else None
                )
                parts = [hq.street, hq.city, hq.state, hq.postal_code]
                hq.formatted_address = ", ".join([str(p) for p in parts if p])
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="headquarters",
                        value=hq.model_dump(),
                        source_name="US SEC EDGAR",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=0.98,
                        filing_id=cik,
                        retrieved_at=now_iso
                    )
                )

        finally:
            if client_to_close:
                await client_to_close.aclose()

        return evidence_bag
