import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional
import httpx

from app.config import get_settings
from app.consensus.entity_matcher import ProbabilisticEntityMatcher
from app.fetchers.base import BaseFetcher, ExtractedEvidence, RawEvidenceBag
from app.models.company import HeadquartersAddress, IndustryTaxonomy
from app.models.provenance import SourceType

logger = logging.getLogger(__name__)


class SecEdgarFetcher(BaseFetcher):
    """
    Tier B: Official Government Registry (US SEC EDGAR)
    Zero-key public registry lookup for US public and reporting corporations.
    - Enforces SEC fair-access rate limiting (<= 10 requests/second) and User-Agent identification.
    - Uses ranked candidate matching (exact CIK > exact ticker > high-threshold token similarity)
      rather than naive first-substring matching.
    - Preserves official SEC legal name casing (never applies .title()).
    - Derives entity status from active exchange listings or recent filing dates rather than assuming Active.
    """

    SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

    _rate_limit_semaphore = asyncio.Semaphore(5)
    _last_request_ts: float = 0.0

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.client = client

    async def _throttled_get(self, client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> httpx.Response:
        async with self._rate_limit_semaphore:
            min_interval = 1.0 / max(1, self.settings.SEC_MAX_REQUESTS_PER_SECOND)
            elapsed = time.monotonic() - SecEdgarFetcher._last_request_ts
            if elapsed < min_interval and not isinstance(client._transport, httpx.MockTransport):
                await asyncio.sleep(min_interval - elapsed)
            SecEdgarFetcher._last_request_ts = time.monotonic()
            return await client.get(url, headers=headers)

    async def search_company(
        self,
        client: httpx.AsyncClient,
        query: Optional[str] = None,
        cik: Optional[str] = None,
        ticker: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """
        Resolves an SEC filer by exact CIK, exact Ticker, or ranked corporate name similarity.
        Returns None if match is ambiguous or below confidence threshold.
        """
        headers = {
            "User-Agent": self.settings.USER_AGENT,
            "Accept": "application/json",
        }

        try:
            # 1. Exact CIK lookup if provided
            if cik:
                clean_cik = re.sub(r"\D", "", cik).zfill(10)
                detail_resp = await self._throttled_get(
                    client,
                    self.SEC_SUBMISSIONS_URL.format(cik=clean_cik),
                    headers=headers
                )
                if detail_resp.status_code == 200:
                    data = detail_resp.json()
                    data["_match_confidence"] = 1.0
                    data["_match_method"] = "exact_cik"
                    return data

            # 2. Fetch SEC company tickers association file
            resp = await self._throttled_get(client, self.SEC_TICKERS_URL, headers=headers)
            if resp.status_code != 200:
                return None

            tickers_data = resp.json()
            if not isinstance(tickers_data, dict):
                return None

            scored_candidates: list[tuple[float, str, dict[str, Any]]] = []
            target_ticker = ticker.strip().upper() if ticker else None

            for _, info in tickers_data.items():
                if not isinstance(info, dict):
                    continue
                title = str(info.get("title", "")).strip()
                cand_ticker = str(info.get("ticker", "")).strip().upper()
                cik_str = str(info.get("cik_str", "")).zfill(10)

                if not title or not cik_str:
                    continue

                score = 0.0
                method = "name_similarity"

                # Exact explicit ticker match
                if target_ticker and cand_ticker == target_ticker:
                    score = 0.96
                    method = "exact_ticker"
                elif query:
                    # Ranked name similarity (prevents naive substring false positives like 'meta' -> 'BIOMETAL')
                    sim = ProbabilisticEntityMatcher.compute_name_similarity(query, title)
                    if sim >= 0.72:
                        score = sim
                    elif len(query.strip()) >= 3 and query.strip().upper() == cand_ticker:
                        score = 0.85
                        method = "query_ticker_match"

                if score >= 0.72:
                    scored_candidates.append((score, method, {
                        "cik": cik_str,
                        "name": title,
                        "ticker": cand_ticker
                    }))

            if not scored_candidates:
                return None

            # Sort by score descending
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_method, best_cand = scored_candidates[0]

            # Guard against ambiguous SEC filers with distinct CIKs and near-identical scores
            if len(scored_candidates) > 1:
                second_score, _, second_cand = scored_candidates[1]
                if second_cand["cik"] != best_cand["cik"] and (best_score - second_score) < 0.08 and best_score < 0.95:
                    logger.info(
                        f"SEC EDGAR match ambiguous for '{query}': {best_cand['name']} ({best_score}) "
                        f"vs {second_cand['name']} ({second_score}); abstaining."
                    )
                    return None

            cik_str = best_cand["cik"]
            detail_resp = await self._throttled_get(
                client,
                self.SEC_SUBMISSIONS_URL.format(cik=cik_str),
                headers=headers
            )
            if detail_resp.status_code == 200:
                detail_data = detail_resp.json()
                detail_data["cik"] = str(detail_data.get("cik", cik_str)).zfill(10)
                detail_data["_match_confidence"] = round(min(1.0, best_score), 3)
                detail_data["_match_method"] = best_method
                return detail_data

            best_cand["_match_confidence"] = round(min(0.92, best_score), 3)
            best_cand["_match_method"] = best_method
            return best_cand

        except Exception as e:
            logger.debug(f"SEC EDGAR search error: {e}")

        return None

    @staticmethod
    def _derive_sec_operating_status(sec_record: dict[str, Any]) -> Optional[tuple[str, float, str]]:
        """
        Determines statutory status from SEC submission data rather than assuming Active.
        Returns (status_value, confidence, rationale_quote) or None if inconclusive.
        """
        forms = sec_record.get("filings", {}).get("recent", {}).get("form", [])
        filing_dates = sec_record.get("filings", {}).get("recent", {}).get("filingDate", [])

        if forms and filing_dates:
            latest_form = str(forms[0])
            latest_date = str(filing_dates[0])
            # Deregistration / termination forms
            if any(latest_form.startswith(dereg) for dereg in ("15-", "25")):
                return ("Inactive", 0.92, f"SEC latest filing {latest_form} on {latest_date} indicates deregistration")
            try:
                year = int(latest_date.split("-")[0])
                current_year = datetime.now(timezone.utc).year
                if current_year - year <= 2:
                    return ("Active", 0.96, f"SEC active filer: latest form {latest_form} filed on {latest_date}")
                else:
                    return ("Inactive", 0.80, f"SEC dormant filer: last filing {latest_form} on {latest_date}")
            except ValueError:
                pass

        # If actively listed on an exchange in submissions metadata
        tickers = sec_record.get("tickers", [])
        if tickers:
            return ("Active", 0.92, f"SEC listed issuer with active ticker(s): {', '.join(tickers)}")

        return None

    async def fetch(
        self,
        canonical_domain: Optional[str],
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        if evidence_bag is None:
            evidence_bag = RawEvidenceBag(canonical_domain=canonical_domain, brand_name=brand_name)

        # If jurisdiction hint explicitly specifies a non-US country, skip US SEC EDGAR
        if evidence_bag.jurisdiction_hint:
            compat = ProbabilisticEntityMatcher.check_jurisdiction_compatibility(
                evidence_bag.jurisdiction_hint, "US"
            )
            if compat is False:
                return evidence_bag

        search_query = brand_name
        if not search_query and canonical_domain:
            search_query = canonical_domain.split(".")[0]

        if not any([search_query, evidence_bag.cik, evidence_bag.ticker]):
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
            sec_record = await self.search_company(
                client,
                query=search_query,
                cik=evidence_bag.cik,
                ticker=evidence_bag.ticker
            )
            if not sec_record:
                return evidence_bag

            evidence_bag.registry_filings.append(sec_record)
            now_iso = datetime.now(timezone.utc).isoformat()
            cik = str(sec_record.get("cik", "")).zfill(10)
            match_conf = float(sec_record.get("_match_confidence", 0.95))
            submission_url = self.SEC_SUBMISSIONS_URL.format(cik=cik)

            # 1. Official Legal Entity Name (preserve exact SEC spelling & casing!)
            legal_name = sec_record.get("name")
            if legal_name:
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="legal_name",
                        value=str(legal_name).strip(),
                        source_name="US Securities and Exchange Commission (SEC EDGAR)",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=round(min(1.0, match_conf), 3),
                        verbatim_quote=f"SEC CIK {cik}: {legal_name}",
                        filing_id=cik,
                        retrieved_at=now_iso,
                        source_url=submission_url,
                        raw_claim=legal_name,
                        transformation_applied="strip_whitespace_only"
                    )
                )

            # 2. Operating Status (only when evidenced by recent filings or active listing)
            status_info = self._derive_sec_operating_status(sec_record)
            if status_info:
                status_val, status_conf, status_quote = status_info
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="status",
                        value=status_val,
                        source_name="US SEC EDGAR",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=round(min(1.0, status_conf * match_conf), 3),
                        verbatim_quote=status_quote,
                        filing_id=cik,
                        retrieved_at=now_iso,
                        source_url=submission_url,
                        raw_claim=status_val
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
                        confidence=round(min(1.0, 0.98 * match_conf), 3),
                        verbatim_quote=f"SIC {sic}: {sic_desc}",
                        filing_id=cik,
                        retrieved_at=now_iso,
                        source_url=submission_url,
                        raw_claim={"sic": sic, "sicDescription": sic_desc}
                    )
                )

            # 4. Principal Executive Office (business) vs Registered/Mailing Address (mailing)
            addresses = sec_record.get("addresses", {})
            business_addr = addresses.get("business", {})
            if business_addr and any(business_addr.values()):
                hq = HeadquartersAddress(
                    street=business_addr.get("street1"),
                    city=business_addr.get("city"),
                    state=business_addr.get("stateOrCountry"),
                    postal_code=business_addr.get("zipCode"),
                    country="USA" if business_addr.get("stateOrCountryDescription") in ("UNITED STATES", None) else business_addr.get("stateOrCountryDescription")
                )
                parts = [hq.street, hq.city, hq.state, hq.postal_code]
                hq.formatted_address = ", ".join([str(p) for p in parts if p])
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="headquarters",
                        value=hq.model_dump(),
                        source_name="US SEC EDGAR (Principal Executive Office)",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=round(min(1.0, 0.96 * match_conf), 3),
                        verbatim_quote=hq.formatted_address,
                        filing_id=cik,
                        retrieved_at=now_iso,
                        source_url=submission_url,
                        raw_claim=business_addr
                    )
                )

            mailing_addr = addresses.get("mailing", {})
            if mailing_addr and any(mailing_addr.values()):
                reg_off = HeadquartersAddress(
                    street=mailing_addr.get("street1"),
                    city=mailing_addr.get("city"),
                    state=mailing_addr.get("stateOrCountry"),
                    postal_code=mailing_addr.get("zipCode"),
                    country="USA" if mailing_addr.get("stateOrCountryDescription") in ("UNITED STATES", None) else mailing_addr.get("stateOrCountryDescription")
                )
                parts = [reg_off.street, reg_off.city, reg_off.state, reg_off.postal_code]
                reg_off.formatted_address = ", ".join([str(p) for p in parts if p])
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="registered_office",
                        value=reg_off.model_dump(),
                        source_name="US SEC EDGAR (Registered/Mailing Address)",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=round(min(1.0, 0.96 * match_conf), 3),
                        verbatim_quote=reg_off.formatted_address,
                        filing_id=cik,
                        retrieved_at=now_iso,
                        source_url=submission_url,
                        raw_claim=mailing_addr
                    )
                )

            # 5. SEC Registered Business Phone
            sec_phone = sec_record.get("phone") or business_addr.get("phone")
            if sec_phone and len(str(sec_phone).strip()) >= 7:
                clean_phone = str(sec_phone).strip()
                evidence_bag.extracted_evidence.append(
                    ExtractedEvidence(
                        field="phone",
                        value=clean_phone,
                        source_name="US SEC EDGAR",
                        source_type=SourceType.GOVERNMENT_REGISTRY.value,
                        confidence=round(min(1.0, 0.96 * match_conf), 3),
                        verbatim_quote=f"SEC Registrant Phone: {clean_phone}",
                        filing_id=cik,
                        retrieved_at=now_iso,
                        source_url=submission_url,
                        raw_claim=sec_phone
                    )
                )

        finally:
            if client_to_close:
                await client_to_close.aclose()

        return evidence_bag
