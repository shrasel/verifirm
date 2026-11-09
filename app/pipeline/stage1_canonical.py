import logging
import re
from typing import Optional
from urllib.parse import urlparse, unquote
import httpx
import tldextract
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from app.config import get_settings
from app.consensus.entity_matcher import ProbabilisticEntityMatcher
from app.models.request import EnrichmentRequest
from app.models.response import CandidateEntity, MatchDecision

logger = logging.getLogger(__name__)


class CanonicalResolutionResult(BaseModel):
    canonical_domain: Optional[str] = None
    original_input: str
    active_web_presence: bool = False
    http_status: Optional[int] = None
    redirect_chain: list[str] = Field(default_factory=list)
    candidates: list[CandidateEntity] = Field(default_factory=list)
    disambiguation_required: bool = False
    unresolved: bool = False
    match_decision: MatchDecision = MatchDecision.AUTO_MATCH
    resolved_brand_name: Optional[str] = None


class CorporateNameCleanser:
    """
    Strips corporate legal designations, punctuation, and extracts core brand tokens
    for candidate discovery without mutating official legal names.
    e.g. 'Meta Platforms, Inc.' -> ('Meta Platforms', 'meta')
    e.g. 'Square, Inc.' -> ('Square', 'square')
    e.g. 'Google DeepMind' -> ('Google DeepMind', 'deepmind')
    """
    LEGAL_SUFFIXES = re.compile(
        r"\b(inc|incorporated|llc|l\.l\.c\.|corp|corporation|ltd|limited|plc|co|company|"
        r"gmbh|ag|a\.g\.|s\.a\.|b\.v\.|pte|pty|holdings?|group|technologies|solutions|services|payments?)\b[.,]?",
        re.IGNORECASE
    )

    @classmethod
    def clean(cls, raw_name: str) -> tuple[str, str]:
        if not raw_name:
            return "", ""
        no_punct = re.sub(r"[,/\\&]+", " ", raw_name)
        words = no_punct.split()

        stripped_words = []
        for w in words:
            if not cls.LEGAL_SUFFIXES.fullmatch(w):
                stripped_words.append(w)

        clean_search = " ".join(stripped_words) if stripped_words else raw_name.strip()
        if len(stripped_words) > 1 and stripped_words[0].lower() in ("google", "microsoft", "amazon", "apple"):
            brand_token = stripped_words[-1].lower()
        elif stripped_words:
            brand_token = stripped_words[0].lower()
        else:
            brand_token = raw_name.lower().split()[0]

        return clean_search, brand_token


class CanonicalIdentityResolver:
    """
    Stage 1: Cautious, Evidence-Backed Identity & Domain Resolution
    - Follows 301/302 redirects across TLD/rebrand changes
    - Cleanses subdomains (shop., blog., app.) to root registered domain
    - Treats Wikidata P856 and Search APIs (Brave Search / DuckDuckGo) strictly as candidate sources, not proof
    - Verifies every candidate domain via landing-page inspection before scoring
    - Never guesses '{brand}.com' when evidence is missing; returns MatchDecision.UNRESOLVED instead
    """

    EXCLUDED_SEARCH_DOMAINS = {
        "wikipedia.org", "wikidata.org", "linkedin.com", "facebook.com", "twitter.com", "x.com",
        "instagram.com", "youtube.com", "crunchbase.com", "pitchbook.com",
        "bloomberg.com", "zoominfo.com", "glassdoor.com", "indeed.com",
        "dnb.com", "yellowpages.com", "bbb.org", "yelp.com", "trustpilot.com",
        "reddit.com", "github.com", "g2.com", "capterra.com", "sec.gov",
        "medium.com", "apple.com", "play.google.com", "opencorporates.com"
    }

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.client = client
        self.extractor = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())

    def clean_domain(self, raw_input: str) -> Optional[str]:
        """
        Strips protocols, paths, query params, subdomains (www, app, blog, shop),
        and returns the canonical registered domain (e.g., 'stripe.com').
        """
        if not raw_input:
            return None

        cleaned = raw_input.strip().lower()
        if not cleaned.startswith(("http://", "https://")):
            cleaned = "https://" + cleaned

        try:
            parsed = urlparse(cleaned)
            hostname = parsed.hostname or cleaned
            ext = self.extractor(hostname)
            if ext.domain and ext.suffix:
                return f"{ext.domain}.{ext.suffix}"
            return hostname
        except Exception as e:
            logger.warning(f"Failed to clean domain '{raw_input}': {e}")
            return None

    async def follow_redirects(self, input_url: str) -> tuple[Optional[str], bool, Optional[int], list[str]]:
        """
        Issues HTTP requests following redirects to reach the canonical destination domain.
        Returns: (canonical_domain, is_active, status_code, redirect_chain)
        """
        target_url = input_url.strip()
        if not target_url.startswith(("http://", "https://")):
            target_url = "https://" + target_url

        redirect_chain = [target_url]
        client_to_close = None
        client = self.client

        if client is None:
            client_to_close = httpx.AsyncClient(
                headers={"User-Agent": self.settings.USER_AGENT},
                timeout=self.settings.HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
                verify=True
            )
            client = client_to_close

        try:
            response = await client.head(target_url)
            if response.status_code in (405, 501, 403):
                response = await client.get(target_url)

            final_url = str(response.url)
            if response.history:
                redirect_chain.extend([str(h.url) for h in response.history])
            redirect_chain.append(final_url)

            canonical = self.clean_domain(final_url)
            is_active = response.status_code < 400
            return canonical, is_active, response.status_code, redirect_chain

        except httpx.HTTPError as e:
            logger.info(f"HTTP probe for '{target_url}' failed: {e}. Falling back to syntax cleanse.")
            fallback_canonical = self.clean_domain(target_url)
            return fallback_canonical, False, None, redirect_chain
        finally:
            if client_to_close:
                await client_to_close.aclose()

    async def query_wikidata_official_website(
        self,
        clean_query: str,
        brand_token: str,
        client: httpx.AsyncClient
    ) -> list[CandidateEntity]:
        """
        Candidate Source 1: Searches Wikidata and extracts Property P856 ("official website").
        Treated strictly as a candidate generator (unverified prior <= 0.55), NOT proof.
        """
        candidates: list[CandidateEntity] = []
        headers = {
            "User-Agent": self.settings.USER_AGENT,
            "Accept": "application/json"
        }
        try:
            search_url = "https://www.wikidata.org/w/api.php"
            params = {
                "action": "wbsearchentities",
                "search": clean_query,
                "language": "en",
                "format": "json"
            }
            resp = await client.get(search_url, params=params, headers=headers, timeout=5.0)
            if resp.status_code != 200:
                return candidates

            data = resp.json().get("search", [])
            for item in data[:4]:
                qid = item.get("id")
                label = item.get("label", "")
                desc = item.get("description", "")

                entity_url = "https://www.wikidata.org/w/api.php"
                claims_params = {
                    "action": "wbgetentities",
                    "ids": qid,
                    "props": "claims",
                    "format": "json"
                }
                ent_resp = await client.get(entity_url, params=claims_params, headers=headers, timeout=5.0)
                if ent_resp.status_code != 200:
                    continue

                entity_data = ent_resp.json().get("entities", {}).get(qid, {})
                claims = entity_data.get("claims", {})
                p856 = claims.get("P856", [])
                if not p856:
                    continue

                official_url = p856[0].get("mainsnak", {}).get("datavalue", {}).get("value")
                if not official_url:
                    continue

                domain = self.clean_domain(official_url)
                if not domain or domain in self.EXCLUDED_SEARCH_DOMAINS:
                    continue

                # Candidate prior score (never sufficient for AUTO_MATCH without landing-page verification)
                prior_score = 0.45
                signals = ["wikidata_p856_candidate"]
                name_sim = ProbabilisticEntityMatcher.compute_name_similarity(clean_query, label)
                if name_sim >= 0.85:
                    prior_score += 0.08
                    signals.append("wikidata_label_exact_match")
                if any(w in desc.lower() for w in ("company", "corporation", "business", "technology", "enterprise", "firm", "financial", "bank", "airline", "software")):
                    prior_score += 0.05
                    signals.append("wikidata_business_entity_description")

                candidates.append(
                    CandidateEntity(
                        name=f"{label} ({desc})" if desc else label,
                        legal_name=label,
                        domain=domain,
                        description=f"Wikidata Knowledge Graph ({qid}) P856 candidate: {official_url}",
                        source=f"Wikidata ({qid})",
                        supporting_signals=signals,
                        score=round(min(0.60, prior_score), 2)
                    )
                )
        except Exception as e:
            logger.debug(f"Wikidata lookup failed: {e}")

        return candidates

    async def verify_candidate_domain_detailed(
        self,
        candidate_domain: str,
        target_name: str,
        brand_token: str,
        client: httpx.AsyncClient
    ) -> tuple[bool, float, Optional[str], Optional[str], list[str], list[str]]:
        """
        Probes https://{candidate_domain}, follows redirects, and checks for explicit signals
        connecting the landing page to the target entity.
        Returns: (is_verified, score, landed_domain, page_title, supporting_signals, contradicting_signals)
        """
        target_url = f"https://{candidate_domain}"
        supporting: list[str] = []
        contradicting: list[str] = []

        try:
            resp = await client.get(target_url, timeout=4.0)
            if resp.status_code >= 400:
                contradicting.append(f"http_status_{resp.status_code}")
                return False, 0.0, candidate_domain, None, supporting, contradicting

            landed_domain = self.clean_domain(str(resp.url)) or candidate_domain
            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""

            meta_desc = ""
            desc_tag = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
            if desc_tag and desc_tag.get("content"):
                meta_desc = desc_tag["content"].strip()

            combined = (title + " " + meta_desc).lower()

            NEGATIVE_PATTERNS = [
                "domain for sale", "buy this domain", "parked free", "website is under construction",
                "apache2 ubuntu default", "iis windows server", "domain parking", "404 not found"
            ]
            if any(neg in combined for neg in NEGATIVE_PATTERNS):
                contradicting.append("parked_or_placeholder_domain")
                return False, 0.0, landed_domain, title, supporting, contradicting

            score = 0.0

            # 1. Exact standalone word match in title
            if brand_token and re.search(r"\b" + re.escape(brand_token) + r"\b", title.lower()):
                score += 0.45
                supporting.append("landing_page_title_match")
            elif brand_token and brand_token in title.lower():
                score += 0.25
                supporting.append("landing_page_title_substring")

            # 2. Target full name match in title or meta description
            clean_name = re.sub(r"[^\w\s]", "", target_name.lower()).strip()
            if clean_name and clean_name in combined:
                score += 0.25
                supporting.append("landing_page_full_name_match")

            # 3. Domain token alignment
            if brand_token and brand_token in landed_domain.lower():
                score += 0.25
                supporting.append("domain_brand_token_alignment")

            # 4. Schema.org JSON-LD name / legalName declaration
            scripts = soup.find_all("script", type=lambda t: t and "application/ld+json" in t.lower())
            for s in scripts:
                txt = s.get_text()
                if brand_token and brand_token in txt.lower():
                    score += 0.20
                    supporting.append("landing_page_jsonld_match")
                    break

            # 5. Footer / body legal entity mention (e.g., Inc., Corp., LLC with target name)
            body_text = soup.get_text(" ", strip=True).lower()
            if clean_name and re.search(r"\b" + re.escape(clean_name) + r"\s+(?:inc|llc|corp|ltd|plc|gmbh)", body_text):
                score += 0.15
                supporting.append("page_legal_entity_suffix_match")

            if score < 0.50:
                contradicting.append("weak_landing_page_entity_signals")

            is_verified = score >= 0.50
            return is_verified, round(min(1.0, score), 2), landed_domain, title, supporting, contradicting
        except Exception as e:
            logger.debug(f"Verification probe failed for {candidate_domain}: {e}")
            contradicting.append("http_probe_unreachable")
            return False, 0.0, candidate_domain, None, supporting, contradicting

    async def verify_candidate_domain(
        self,
        candidate_domain: str,
        target_name: str,
        brand_token: str,
        client: httpx.AsyncClient
    ) -> tuple[bool, float, Optional[str], Optional[str]]:
        """
        Backward-compatible 4-tuple wrapper around verify_candidate_domain_detailed.
        """
        is_ver, score, landed, title, _, _ = await self.verify_candidate_domain_detailed(
            candidate_domain, target_name, brand_token, client
        )
        return is_ver, score, landed, title

    async def _query_brave_search(
        self,
        query: str,
        client: httpx.AsyncClient
    ) -> list[tuple[str, str, str]]:
        """
        Queries Brave Search API if BRAVE_SEARCH_API_KEY is configured.
        Returns list of (url, title, snippet).
        """
        api_key = self.settings.BRAVE_SEARCH_API_KEY
        if not api_key:
            return []

        results_out: list[tuple[str, str, str]] = []
        try:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": 6},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                    "User-Agent": self.settings.USER_AGENT
                },
                timeout=5.0
            )
            if resp.status_code == 200:
                web_results = resp.json().get("web", {}).get("results", [])
                for item in web_results:
                    url = item.get("url", "")
                    title = item.get("title", "")
                    desc = item.get("description", "")
                    if url:
                        results_out.append((url, title, desc))
        except Exception as e:
            logger.debug(f"Brave Search API error: {e}")

        return results_out

    async def _query_duckduckgo_search(
        self,
        query: str,
        client: httpx.AsyncClient
    ) -> list[tuple[str, str, str]]:
        """
        Zero-key fallback search discovery via DuckDuckGo HTML endpoint.
        Returns list of (url, title, snippet).
        """
        results_out: list[tuple[str, str, str]] = []
        try:
            url = "https://html.duckduckgo.com/html/"
            response = await client.post(url, data={"q": query})
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                results = soup.select(".web-result, .result")
                for r in results:
                    title_elem = r.select_one(".result__title, .result__a")
                    snippet_elem = r.select_one(".result__snippet")
                    link_elem = r.select_one("a.result__url, a.result__a")
                    if not link_elem:
                        continue
                    raw_href = link_elem.get("href", "")
                    if "uddg=" in raw_href:
                        match = re.search(r"uddg=([^&]+)", raw_href)
                        if match:
                            raw_href = unquote(match.group(1))
                    title_txt = title_elem.get_text(strip=True) if title_elem else ""
                    snippet_txt = snippet_elem.get_text(strip=True) if snippet_elem else ""
                    if raw_href:
                        results_out.append((raw_href, title_txt, snippet_txt))
        except Exception as e:
            logger.debug(f"DuckDuckGo search error: {e}")

        return results_out

    async def search_name_to_domain(
        self,
        company_name: str,
        jurisdiction_hint: Optional[str] = None
    ) -> list[CandidateEntity]:
        """
        Generates candidate domains from Wikidata P856 and Search (Brave Search API or DuckDuckGo),
        then verifies every candidate against its live landing page.
        Never fabricates unverified '{brand}.com' fallbacks.
        """
        clean_query, brand_token = CorporateNameCleanser.clean(company_name)
        candidates: list[CandidateEntity] = []

        client_to_close = None
        client = self.client

        if client is None:
            client_to_close = httpx.AsyncClient(
                headers={"User-Agent": self.settings.USER_AGENT},
                timeout=self.settings.HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
                verify=True
            )
            client = client_to_close

        try:
            seen_domains: set[str] = set()

            # --- CANDIDATE SOURCE 1: WIKIDATA KNOWLEDGE GRAPH (P856) ---
            wiki_candidates = await self.query_wikidata_official_website(clean_query, brand_token, client)
            for c in wiki_candidates:
                if not c.domain or c.domain in seen_domains:
                    continue
                is_ver, ver_score, landed_domain, title, sup_sigs, con_sigs = await self.verify_candidate_domain_detailed(
                    c.domain, company_name, brand_token, client
                )
                final_domain = landed_domain or c.domain
                if final_domain in seen_domains:
                    continue
                seen_domains.add(final_domain)

                combined_supporting = list(dict.fromkeys(c.supporting_signals + sup_sigs))
                combined_contradicting = list(dict.fromkeys(c.contradicting_signals + con_sigs))

                if is_ver:
                    # Corroborated by both Wikidata P856 and live landing page verification
                    final_score = round(min(0.96, 0.20 + ver_score), 2)
                else:
                    # Unverified Wikidata candidate cannot exceed REVIEW_THRESHOLD on its own
                    final_score = round(min(0.38, c.score * 0.65), 2)
                    if "landing_page_verification_failed" not in combined_contradicting:
                        combined_contradicting.append("landing_page_verification_failed")

                candidates.append(
                    CandidateEntity(
                        name=title or c.name,
                        legal_name=c.legal_name,
                        domain=final_domain,
                        description=c.description,
                        jurisdiction=jurisdiction_hint,
                        source=c.source or "Wikidata P856",
                        supporting_signals=combined_supporting,
                        contradicting_signals=combined_contradicting,
                        score=final_score
                    )
                )

            # --- CANDIDATE SOURCE 2: LICENSED SEARCH (BRAVE) OR DUCKDUCKGO FALLBACK ---
            query_parts = [f'"{clean_query}"', "official website"]
            if jurisdiction_hint:
                query_parts.append(jurisdiction_hint)
            query = " ".join(query_parts)

            search_hits = await self._query_brave_search(query, client)
            search_source_label = "Brave Search API"
            if not search_hits:
                search_hits = await self._query_duckduckgo_search(query, client)
                search_source_label = "Web Search Discovery"

            for raw_href, hit_title, snippet in search_hits:
                raw_domain = self.clean_domain(raw_href)
                if not raw_domain or raw_domain in self.EXCLUDED_SEARCH_DOMAINS or raw_domain in seen_domains:
                    continue

                is_ver, ver_score, landed_domain, landed_title, sup_sigs, con_sigs = await self.verify_candidate_domain_detailed(
                    raw_domain, company_name, brand_token, client
                )
                final_domain = landed_domain or raw_domain
                if final_domain in seen_domains:
                    continue
                seen_domains.add(final_domain)

                supporting = ["search_candidate"] + sup_sigs
                contradicting = list(con_sigs)

                if is_ver:
                    final_score = ver_score
                else:
                    final_score = 0.30
                    if "landing_page_verification_failed" not in contradicting:
                        contradicting.append("landing_page_verification_failed")

                candidates.append(
                    CandidateEntity(
                        name=landed_title or hit_title or final_domain,
                        domain=final_domain,
                        description=snippet,
                        jurisdiction=jurisdiction_hint,
                        source=search_source_label,
                        supporting_signals=supporting,
                        contradicting_signals=contradicting,
                        score=final_score
                    )
                )

                if len(candidates) >= 6:
                    break

        finally:
            if client_to_close:
                await client_to_close.aclose()

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    async def resolve(self, request: EnrichmentRequest) -> CanonicalResolutionResult:
        """
        Coordinates cautious, evidence-backed identity and domain resolution.
        Returns explicit MatchDecision (AUTO_MATCH, NEEDS_REVIEW, UNRESOLVED).
        """
        # Case A: Website is explicitly provided
        if request.website:
            canonical, is_active, status_code, chain = await self.follow_redirects(request.website)
            decision = MatchDecision.AUTO_MATCH if canonical else MatchDecision.UNRESOLVED
            return CanonicalResolutionResult(
                canonical_domain=canonical,
                original_input=request.website,
                active_web_presence=is_active,
                http_status=status_code,
                redirect_chain=chain,
                match_decision=decision,
                unresolved=(decision == MatchDecision.UNRESOLVED),
                resolved_brand_name=request.name
            )

        # Case B: Company Name is provided (with or without registry hints)
        if request.name:
            candidates = await self.search_name_to_domain(request.name, request.jurisdiction_hint)
            decision = ProbabilisticEntityMatcher.decide_match(candidates)

            if decision == MatchDecision.UNRESOLVED or not candidates:
                clean_query, _ = CorporateNameCleanser.clean(request.name)
                return CanonicalResolutionResult(
                    canonical_domain=None,
                    original_input=request.name,
                    active_web_presence=False,
                    candidates=candidates,
                    disambiguation_required=False,
                    unresolved=True,
                    match_decision=MatchDecision.UNRESOLVED,
                    resolved_brand_name=clean_query or request.name
                )

            top = candidates[0]
            disambiguation = (decision == MatchDecision.NEEDS_REVIEW)

            return CanonicalResolutionResult(
                canonical_domain=top.domain if decision == MatchDecision.AUTO_MATCH else None,
                original_input=request.name,
                active_web_presence=(decision == MatchDecision.AUTO_MATCH and top.score >= 0.50),
                candidates=candidates,
                disambiguation_required=disambiguation,
                unresolved=False,
                match_decision=decision,
                resolved_brand_name=request.name
            )

        # Case C: Exact registry identifier provided (cik, lei, registration_number, ticker) without name or website
        identifier_str = request.cik or request.lei or request.registration_number or request.ticker or "identifier"
        return CanonicalResolutionResult(
            canonical_domain=None,
            original_input=identifier_str,
            active_web_presence=False,
            candidates=[],
            disambiguation_required=False,
            unresolved=False,
            match_decision=MatchDecision.AUTO_MATCH,
            resolved_brand_name=request.ticker
        )
