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


class GleifFetcher(BaseFetcher):
    """
    Tier B: Global Legal Entity Identifier Foundation (GLEIF) API
    Zero-key, public global registry for ISO 17442 LEI records.
    Provides exact legal entity names, jurisdiction, registration authority numbers,
    registered legal address vs. headquarters address, and entity status.
    """

    BASE_URL = "https://api.gleif.org/api/v1/lei-records"

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.client = client

    @staticmethod
    def _parse_gleif_address(addr_obj: dict[str, Any]) -> Optional[HeadquartersAddress]:
        if not addr_obj or not isinstance(addr_obj, dict):
            return None
        lines = addr_obj.get("addressLines", [])
        street = ", ".join([str(l) for l in lines if l]) if lines else None
        city = addr_obj.get("city")
        region = addr_obj.get("region")
        postal = addr_obj.get("postalCode")
        country = addr_obj.get("country")

        if not any([street, city, country]):
            return None

        hq = HeadquartersAddress(
            street=street,
            city=city,
            state=region,
            postal_code=postal,
            country=country
        )
        parts = [street, city, region, postal, country]
        hq.formatted_address = ", ".join([str(p) for p in parts if p])
        return hq

    async def fetch(
        self,
        canonical_domain: Optional[str],
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        if evidence_bag is None:
            evidence_bag = RawEvidenceBag(canonical_domain=canonical_domain, brand_name=brand_name)

        query = brand_name or (canonical_domain.split(".")[0] if canonical_domain else None)
        lei = evidence_bag.lei

        if not lei and not query:
            return evidence_bag

        client_to_close = None
        client = self.client

        if client is None:
            client_to_close = httpx.AsyncClient(
                headers={
                    "User-Agent": self.settings.USER_AGENT,
                    "Accept": "application/vnd.api+json"
                },
                timeout=self.settings.HTTP_TIMEOUT_SECONDS
            )
            client = client_to_close

        try:
            headers = {
                "User-Agent": self.settings.USER_AGENT,
                "Accept": "application/vnd.api+json"
            }
            matched_record = None
            match_conf = 0.0

            # 1. Exact LEI lookup
            if lei:
                resp = await client.get(f"{self.BASE_URL}/{lei.strip().upper()}", headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data")
                    if isinstance(data, dict):
                        matched_record = data
                        match_conf = 1.0

            # 2. Name search with ranked similarity + jurisdiction filtering
            if not matched_record and query:
                params = {
                    "filter[entity.legalName]": query,
                    "page[size]": 5
                }
                resp = await client.get(self.BASE_URL, params=params, headers=headers)
                if resp.status_code == 200:
                    records = resp.json().get("data", [])
                    scored: list[tuple[float, dict[str, Any]]] = []
                    for rec in records:
                        attrs = rec.get("attributes", {})
                        ent = attrs.get("entity", {})
                        legal_name = ent.get("legalName", {}).get("name", "")
                        jurisdiction = ent.get("jurisdiction", "")
                        other_names = [
                            n.get("name", "") for n in ent.get("otherNames", []) if isinstance(n, dict)
                        ]

                        sim = ProbabilisticEntityMatcher.compute_name_similarity(
                            query, legal_name, former_names=other_names
                        )
                        if sim < 0.75:
                            continue

                        if evidence_bag.jurisdiction_hint:
                            compat = ProbabilisticEntityMatcher.check_jurisdiction_compatibility(
                                evidence_bag.jurisdiction_hint, jurisdiction
                            )
                            if compat is False:
                                continue
                            if compat is True:
                                sim = min(1.0, sim + 0.08)

                        scored.append((sim, rec))

                    if scored:
                        scored.sort(key=lambda x: x[0], reverse=True)
                        best_score, best_rec = scored[0]
                        # Ensure no ambiguous tie
                        if len(scored) == 1 or (best_score - scored[1][0]) >= 0.08:
                            matched_record = best_rec
                            match_conf = round(min(0.96, best_score), 3)

            if not matched_record:
                return evidence_bag

            attrs = matched_record.get("attributes", {})
            ent = attrs.get("entity", {})
            lei_code = str(attrs.get("lei") or matched_record.get("id") or "")
            legal_name = ent.get("legalName", {}).get("name")
            jurisdiction = ent.get("jurisdiction")
            reg_as = ent.get("registeredAs")
            entity_status = str(ent.get("status", "")).upper()
            last_update = attrs.get("registration", {}).get("lastUpdateDate")
            source_url = f"{self.BASE_URL}/{lei_code}" if lei_code else self.BASE_URL
            now_iso = datetime.now(timezone.utc).isoformat()

            evidence_bag.registry_filings.append({
                "source": "GLEIF",
                "lei": lei_code,
                "legal_name": legal_name,
                "jurisdiction": jurisdiction,
                "registration_number": reg_as,
                "status": entity_status
            })

            if legal_name:
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="legal_name",
                        value=str(legal_name).strip(),
                        source_name=f"GLEIF Global LEI Index ({jurisdiction or 'Global'})",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=match_conf,
                        verbatim_quote=f"LEI {lei_code}: {legal_name}",
                        filing_id=lei_code,
                        retrieved_at=now_iso,
                        source_url=source_url,
                        source_updated_at=last_update,
                        raw_claim=legal_name,
                        transformation_applied="strip_whitespace_only"
                    )
                )

            if entity_status in ("ACTIVE", "INACTIVE"):
                mapped_status = "Active" if entity_status == "ACTIVE" else "Inactive"
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="status",
                        value=mapped_status,
                        source_name="GLEIF Global LEI Index",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=round(min(0.98, match_conf), 3),
                        verbatim_quote=f"LEI {lei_code} entity.status={entity_status}",
                        filing_id=lei_code,
                        retrieved_at=now_iso,
                        source_url=source_url,
                        source_updated_at=last_update,
                        raw_claim=entity_status
                    )
                )

            hq_addr = self._parse_gleif_address(ent.get("headquartersAddress", {}))
            if hq_addr:
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="headquarters",
                        value=hq_addr.model_dump(),
                        source_name="GLEIF (Headquarters Address)",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=round(min(0.96, match_conf), 3),
                        verbatim_quote=hq_addr.formatted_address,
                        filing_id=lei_code,
                        retrieved_at=now_iso,
                        source_url=source_url,
                        source_updated_at=last_update,
                        raw_claim=ent.get("headquartersAddress")
                    )
                )

            legal_addr = self._parse_gleif_address(ent.get("legalAddress", {}))
            if legal_addr:
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="registered_office",
                        value=legal_addr.model_dump(),
                        source_name="GLEIF (Registered Legal Address)",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=round(min(0.96, match_conf), 3),
                        verbatim_quote=legal_addr.formatted_address,
                        filing_id=lei_code,
                        retrieved_at=now_iso,
                        source_url=source_url,
                        source_updated_at=last_update,
                        raw_claim=ent.get("legalAddress")
                    )
                )

        except Exception as e:
            logger.debug(f"GLEIF lookup error: {e}")
        finally:
            if client_to_close:
                await client_to_close.aclose()

        return evidence_bag

