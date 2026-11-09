import logging
from datetime import datetime, timezone
from typing import Any, Optional
import httpx

from app.config import get_settings
from app.consensus.entity_matcher import ProbabilisticEntityMatcher
from app.fetchers.base import BaseFetcher, ExtractedEvidence, RawEvidenceBag
from app.models.company import HeadquartersAddress
from app.models.provenance import SourceType

logger = logging.getLogger(__name__)


class CompaniesHouseFetcher(BaseFetcher):
    """
    Tier B: Official UK Government Registry (Companies House API)
    Queries official UK register by exact company_number or ranked company name search
    when COMPANIES_HOUSE_API_KEY is configured.
    Retains company_number and jurisdiction ('GB'), and preserves official legal spelling.
    """

    BASE_URL = "https://api.company-information.service.gov.uk"

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.client = client

    async def fetch(
        self,
        canonical_domain: Optional[str],
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        if evidence_bag is None:
            evidence_bag = RawEvidenceBag(canonical_domain=canonical_domain, brand_name=brand_name)

        api_key = self.settings.COMPANIES_HOUSE_API_KEY
        if not api_key:
            return evidence_bag

        if evidence_bag.jurisdiction_hint:
            compat = ProbabilisticEntityMatcher.check_jurisdiction_compatibility(
                evidence_bag.jurisdiction_hint, "GB"
            )
            if compat is False:
                return evidence_bag

        query = brand_name or (canonical_domain.split(".")[0] if canonical_domain else None)
        reg_no = evidence_bag.registration_number
        if not reg_no and not query:
            return evidence_bag

        client_to_close = None
        client = self.client

        if client is None:
            client_to_close = httpx.AsyncClient(timeout=self.settings.HTTP_TIMEOUT_SECONDS)
            client = client_to_close

        try:
            auth = (api_key, "")
            profile: Optional[dict[str, Any]] = None
            match_conf = 0.0

            if reg_no:
                resp = await client.get(f"{self.BASE_URL}/company/{reg_no.strip()}", auth=auth)
                if resp.status_code == 200:
                    profile = resp.json()
                    match_conf = 1.0

            if not profile and query:
                resp = await client.get(
                    f"{self.BASE_URL}/search/companies",
                    params={"q": query, "items_per_page": 5},
                    auth=auth
                )
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    scored: list[tuple[float, dict[str, Any]]] = []
                    for item in items:
                        title = item.get("title", "")
                        sim = ProbabilisticEntityMatcher.compute_name_similarity(query, title)
                        if sim >= 0.78:
                            scored.append((sim, item))
                    if scored:
                        scored.sort(key=lambda x: x[0], reverse=True)
                        best_score, best_item = scored[0]
                        if len(scored) == 1 or (best_score - scored[1][0]) >= 0.08:
                            profile = best_item
                            match_conf = round(min(0.96, best_score), 3)

            if not profile:
                return evidence_bag

            comp_name = profile.get("company_name") or profile.get("title")
            comp_num = str(profile.get("company_number", ""))
            raw_status = str(profile.get("company_status", "")).lower()
            date_creation = profile.get("date_of_creation")
            addr = profile.get("registered_office_address") or profile.get("address", {})
            now_iso = datetime.now(timezone.utc).isoformat()
            source_url = f"{self.BASE_URL}/company/{comp_num}" if comp_num else self.BASE_URL

            evidence_bag.registry_filings.append({
                "source": "UK Companies House",
                "company_number": comp_num,
                "jurisdiction": "GB",
                "legal_name": comp_name,
                "status": raw_status
            })

            if comp_name:
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="legal_name",
                        value=str(comp_name).strip(),
                        source_name="UK Companies House (Official Registry)",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=match_conf,
                        verbatim_quote=f"Companies House #{comp_num}: {comp_name}",
                        filing_id=comp_num,
                        retrieved_at=now_iso,
                        source_url=source_url,
                        raw_claim=comp_name
                    )
                )

            STATUS_MAP = {
                "active": "Active",
                "dissolved": "Dissolved",
                "liquidation": "Inactive",
                "administration": "Inactive",
            }
            if raw_status in STATUS_MAP:
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="status",
                        value=STATUS_MAP[raw_status],
                        source_name="UK Companies House",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=match_conf,
                        verbatim_quote=f"company_status: {raw_status}",
                        filing_id=comp_num,
                        retrieved_at=now_iso,
                        source_url=source_url,
                        raw_claim=raw_status
                    )
                )

            if date_creation and len(str(date_creation)) >= 4:
                try:
                    year = int(str(date_creation)[:4])
                    evidence_bag.extracted_evidence.append(
                        ExtractedEvidence(
                            field="founded_year",
                            value=year,
                            source_name="UK Companies House",
                            source_type=SourceType.GOVERNMENT_REGISTRY.value,
                            confidence=match_conf,
                            verbatim_quote=f"date_of_creation: {date_creation}",
                            filing_id=comp_num,
                            retrieved_at=now_iso,
                            source_url=source_url,
                            raw_claim=date_creation
                        )
                    )
                except ValueError:
                    pass

            if isinstance(addr, dict) and any(addr.values()):
                reg_hq = HeadquartersAddress(
                    street=addr.get("address_line_1"),
                    city=addr.get("locality"),
                    state=addr.get("region"),
                    postal_code=addr.get("postal_code"),
                    country=addr.get("country") or "United Kingdom"
                )
                parts = [reg_hq.street, reg_hq.city, reg_hq.state, reg_hq.postal_code, reg_hq.country]
                reg_hq.formatted_address = ", ".join([str(p) for p in parts if p])
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="registered_office",
                        value=reg_hq.model_dump(),
                        source_name="UK Companies House (Registered Office)",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=match_conf,
                        verbatim_quote=reg_hq.formatted_address,
                        filing_id=comp_num,
                        retrieved_at=now_iso,
                        source_url=source_url,
                        raw_claim=addr
                    )
                )

        except Exception as e:
            logger.debug(f"UK Companies House lookup error: {e}")
        finally:
            if client_to_close:
                await client_to_close.aclose()

        return evidence_bag

