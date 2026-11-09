import logging
from datetime import datetime, timezone
from typing import Any, Optional
import httpx

from app.config import get_settings
from app.consensus.entity_matcher import ProbabilisticEntityMatcher
from app.fetchers.base import BaseFetcher, ExtractedEvidence, RawEvidenceBag
from app.models.provenance import SourceType

logger = logging.getLogger(__name__)


class OpenCorporatesFetcher(BaseFetcher):
    """
    Tier B Secondary: Corporate Registry Aggregator (OpenCorporates)
    Classified as REGISTRY_AGGREGATOR (lower authority than primary statutory registers).
    Ranks candidate records by name similarity and jurisdiction compatibility rather than
    blindly accepting the first search hit, preserves exact casing, and records the
    underlying jurisdiction and company number.
    """

    BASE_URL = "https://api.opencorporates.com/v0.4/companies/search"

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

        api_key = self.settings.OPENCORPORATES_API_KEY
        if not api_key:
            logger.debug("OpenCorporates API key not configured; skipping aggregator lookup.")
            return evidence_bag

        query = brand_name or (canonical_domain.split(".")[0] if canonical_domain else None)
        if not query:
            return evidence_bag

        client_to_close = None
        client = self.client

        if client is None:
            client_to_close = httpx.AsyncClient(timeout=self.settings.HTTP_TIMEOUT_SECONDS)
            client = client_to_close

        try:
            params: dict[str, Any] = {"q": query, "api_token": api_key}
            if evidence_bag.jurisdiction_hint:
                params["jurisdiction_code"] = evidence_bag.jurisdiction_hint.lower()

            resp = await client.get(self.BASE_URL, params=params)
            if resp.status_code == 200:
                data = resp.json()
                companies = data.get("results", {}).get("companies", [])
                scored: list[tuple[float, dict[str, Any]]] = []

                for item in companies:
                    comp = item.get("company", {})
                    name = comp.get("name", "")
                    jur = comp.get("jurisdiction_code", "")
                    prev_names = [
                        p.get("company_name", "") for p in comp.get("previous_names", []) if isinstance(p, dict)
                    ]
                    sim = ProbabilisticEntityMatcher.compute_name_similarity(query, name, former_names=prev_names)
                    if sim < 0.75:
                        continue
                    if evidence_bag.jurisdiction_hint:
                        compat = ProbabilisticEntityMatcher.check_jurisdiction_compatibility(
                            evidence_bag.jurisdiction_hint, jur
                        )
                        if compat is False:
                            continue
                        if compat is True:
                            sim = min(1.0, sim + 0.08)
                    scored.append((sim, comp))

                if scored:
                    scored.sort(key=lambda x: x[0], reverse=True)
                    best_score, top = scored[0]
                    # Only emit if unambiguous or clear winner
                    if len(scored) == 1 or (best_score - scored[1][0]) >= 0.08:
                        now_iso = datetime.now(timezone.utc).isoformat()
                        jur_code = str(top.get("jurisdiction_code", "")).upper()
                        comp_num = str(top.get("company_number", ""))
                        oc_url = top.get("opencorporates_url") or self.BASE_URL
                        underlying_reg = top.get("registry_url")
                        updated_at = top.get("updated_at")
                        agg_conf = round(min(0.88, best_score * 0.90), 3)

                        evidence_bag.registry_filings.append({
                            "source": "OpenCorporates Aggregator",
                            "jurisdiction": jur_code,
                            "company_number": comp_num,
                            "legal_name": top.get("name"),
                            "registry_url": underlying_reg
                        })

                        comp_name = top.get("name")
                        if comp_name:
                            evidence_bag.extracted_evidence.append(
                                ExtractedEvidence(
                                    field="legal_name",
                                    value=str(comp_name).strip(),  # Never .title()!
                                    source_name=f"OpenCorporates Aggregator ({jur_code} #{comp_num})",
                                    source_type=SourceType.REGISTRY_AGGREGATOR.value,
                                    confidence=agg_conf,
                                    verbatim_quote=f"OpenCorporates {jur_code}/{comp_num}: {comp_name}",
                                    filing_id=f"{jur_code}:{comp_num}" if jur_code else comp_num,
                                    retrieved_at=now_iso,
                                    source_url=oc_url,
                                    source_updated_at=updated_at,
                                    raw_claim=comp_name
                                )
                            )

                        inc_date = top.get("incorporation_date")
                        if inc_date:
                            try:
                                year = int(str(inc_date).split("-")[0])
                                evidence_bag.extracted_evidence.append(
                                    ExtractedEvidence(
                                        field="founded_year",
                                        value=year,
                                        source_name=f"OpenCorporates Aggregator ({jur_code})",
                                        source_type=SourceType.REGISTRY_AGGREGATOR.value,
                                        confidence=agg_conf,
                                        verbatim_quote=f"incorporation_date: {inc_date}",
                                        filing_id=f"{jur_code}:{comp_num}" if jur_code else comp_num,
                                        retrieved_at=now_iso,
                                        source_url=oc_url,
                                        source_updated_at=updated_at,
                                        raw_claim=inc_date
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
