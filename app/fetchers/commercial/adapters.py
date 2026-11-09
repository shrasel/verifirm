import logging
from datetime import datetime, timezone
from typing import Optional
import httpx

from app.config import get_settings
from app.fetchers.base import BaseFetcher, ExtractedEvidence, RawEvidenceBag
from app.models.company import HeadquartersAddress
from app.models.provenance import SourceType

logger = logging.getLogger(__name__)


class PdlFetcher(BaseFetcher):
    """
    Tier C: Commercial B2B Enrichment Adapter (People Data Labs)
    Treats commercial results strictly as vendor evidence (SourceType.COMMERCIAL_API, confidence <= 0.78),
    never ground truth. Executes live enrichment when PDL_API_KEY is configured.
    """

    BASE_URL = "https://api.peopledatalabs.com/v5/company/enrich"

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

        api_key = self.settings.PDL_API_KEY
        if not api_key or not (canonical_domain or brand_name):
            return evidence_bag

        client_to_close = None
        client = self.client
        if client is None:
            client_to_close = httpx.AsyncClient(timeout=self.settings.HTTP_TIMEOUT_SECONDS)
            client = client_to_close

        try:
            params = {}
            if canonical_domain:
                params["website"] = canonical_domain
            elif brand_name:
                params["name"] = brand_name

            resp = await client.get(
                self.BASE_URL,
                params=params,
                headers={"X-Api-Key": api_key, "Accept": "application/json"}
            )
            if resp.status_code == 200:
                data = resp.json()
                now_iso = datetime.now(timezone.utc).isoformat()
                evidence_bag.commercial_records.append({"source": "PDL", "data": data})

                if data.get("size"):
                    evidence_bag.extracted_evidence.append(
                        ExtractedEvidence(
                            field="employee_range",
                            value=str(data["size"]),
                            source_name="People Data Labs (Commercial Vendor)",
                            source_type=SourceType.COMMERCIAL_API.value,
                            confidence=0.78,
                            retrieved_at=now_iso,
                            source_url=self.BASE_URL,
                            raw_claim=data["size"]
                        )
                    )
                if data.get("founded"):
                    try:
                        evidence_bag.extracted_evidence.append(
                            ExtractedEvidence(
                                field="founded_year",
                                value=int(data["founded"]),
                                source_name="People Data Labs (Commercial Vendor)",
                                source_type=SourceType.COMMERCIAL_API.value,
                                confidence=0.75,
                                retrieved_at=now_iso,
                                source_url=self.BASE_URL,
                                raw_claim=data["founded"]
                            )
                        )
                    except (ValueError, TypeError):
                        pass
                loc = data.get("location")
                if isinstance(loc, dict) and loc.get("locality"):
                    hq = HeadquartersAddress(
                        street=loc.get("street_address"),
                        city=loc.get("locality"),
                        state=loc.get("region"),
                        postal_code=loc.get("postal_code"),
                        country=loc.get("country")
                    )
                    parts = [hq.street, hq.city, hq.state, hq.postal_code, hq.country]
                    hq.formatted_address = ", ".join([str(p) for p in parts if p])
                    evidence_bag.extracted_evidence.append(
                        ExtractedEvidence(
                            field="headquarters",
                            value=hq.model_dump(),
                            source_name="People Data Labs (Commercial Vendor)",
                            source_type=SourceType.COMMERCIAL_API.value,
                            confidence=0.75,
                            retrieved_at=now_iso,
                            source_url=self.BASE_URL,
                            raw_claim=loc
                        )
                    )
        except Exception as e:
            logger.debug(f"PDL enrichment adapter error: {e}")
        finally:
            if client_to_close:
                await client_to_close.aclose()

        return evidence_bag


class ApolloFetcher(BaseFetcher):
    """
    Tier C: Commercial B2B Enrichment Adapter (Apollo.io)
    Treats commercial results strictly as vendor evidence (SourceType.COMMERCIAL_API, confidence <= 0.78).
    """

    BASE_URL = "https://api.apollo.io/api/v1/organizations/enrich"

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

        api_key = self.settings.APOLLO_API_KEY
        if not api_key or not canonical_domain:
            return evidence_bag

        client_to_close = None
        client = self.client
        if client is None:
            client_to_close = httpx.AsyncClient(timeout=self.settings.HTTP_TIMEOUT_SECONDS)
            client = client_to_close

        try:
            resp = await client.get(
                self.BASE_URL,
                params={"domain": canonical_domain},
                headers={"X-Api-Key": api_key, "Accept": "application/json"}
            )
            if resp.status_code == 200:
                org = resp.json().get("organization", {})
                if org:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    evidence_bag.commercial_records.append({"source": "Apollo", "data": org})
                    if org.get("estimated_num_employees"):
                        evidence_bag.extracted_evidence.append(
                            ExtractedEvidence(
                                field="employee_range",
                                value=str(org["estimated_num_employees"]),
                                source_name="Apollo.io (Commercial Vendor Estimate)",
                                source_type=SourceType.COMMERCIAL_API.value,
                                confidence=0.72,
                                retrieved_at=now_iso,
                                source_url=self.BASE_URL,
                                raw_claim=org["estimated_num_employees"]
                            )
                        )
        except Exception as e:
            logger.debug(f"Apollo enrichment adapter error: {e}")
        finally:
            if client_to_close:
                await client_to_close.aclose()

        return evidence_bag
