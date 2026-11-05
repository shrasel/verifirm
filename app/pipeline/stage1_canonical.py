import logging
import re
from typing import Optional
from urllib.parse import urlparse, unquote
import httpx
import tldextract
from bs4 import BeautifulSoup
from pydantic import BaseModel

from app.config import get_settings
from app.models.request import EnrichmentRequest
from app.models.response import CandidateEntity

logger = logging.getLogger(__name__)


class CanonicalResolutionResult(BaseModel):
    canonical_domain: Optional[str] = None
    original_input: str
    active_web_presence: bool = True
    http_status: Optional[int] = None
    redirect_chain: list[str] = []
    candidates: list[CandidateEntity] = []
    disambiguation_required: bool = False
    resolved_brand_name: Optional[str] = None


class CorporateNameCleanser:
    """
    Strips corporate legal designations, punctuation, and extracts core brand tokens
    for high-precision entity and domain matching.
    e.g. 'Meta Platforms, Inc.' -> ('Meta Platforms', 'meta')
    e.g. 'Square, Inc.' -> ('Square', 'square')
    e.g. 'Google DeepMind' -> ('Google DeepMind', 'deepmind')
    """
    LEGAL_SUFFIXES = re.compile(
        r"\b(inc|incorporated|llc|l\.l\.c\.|corp|corporation|ltd|limited|co|company|gmbh|ag|a\.g\.|s\.a\.|b\.v\.|pte|pty|holdings?|group|technologies|solutions|services|payments?)\b[.,]?",
        re.IGNORECASE
    )

    @classmethod
    def clean(cls, raw_name: str) -> tuple[str, str]:
        if not raw_name:
            return "", ""
        # Replace commas, slashes, ampersands with spaces
        no_punct = re.sub(r"[,/\\&]+", " ", raw_name)
        words = no_punct.split()

        # Strip trailing legal designations
        stripped_words = []
        for w in words:
            if not cls.LEGAL_SUFFIXES.fullmatch(w):
                stripped_words.append(w)

        clean_search = " ".join(stripped_words) if stripped_words else raw_name.strip()
        # Primary brand token: last word for qualified names ("Google DeepMind" -> "deepmind")
        # or first word for standard names ("Meta Platforms" -> "meta")
        if len(stripped_words) > 1 and stripped_words[0].lower() in ("google", "microsoft", "amazon", "apple"):
            brand_token = stripped_words[-1].lower()
        elif stripped_words:
            brand_token = stripped_words[0].lower()
        else:
            brand_token = raw_name.lower().split()[0]

        return clean_search, brand_token


class CanonicalIdentityResolver:
    """
    Stage 1: Identity Resolution & Canonicalization
    - Follows 301/302 redirects across TLD/rebrand changes
    - Cleanses subdomains (shop., blog., app.) to root registered domain
    - Tier 1: Wikidata Knowledge Graph P856 ("official website") lookup (zero-key)
    - Tier 2: Search Engine Waterfall (DuckDuckGo / SerpAPI)
    - Tier 3: Bidirectional Ground-Truth Verification Guard (probes landing page title & JSON-LD)
    """

    EXCLUDED_SEARCH_DOMAINS = {
        "wikipedia.org", "wikidata.org", "linkedin.com", "facebook.com", "twitter.com", "x.com",
        "instagram.com", "youtube.com", "crunchbase.com", "pitchbook.com",
        "bloomberg.com", "zoominfo.com", "glassdoor.com", "indeed.com",
        "dnb.com", "yellowpages.com", "bbb.org", "yelp.com", "trustpilot.com",
        "reddit.com", "github.com", "g2.com", "capterra.com", "sec.gov",
        "medium.com", "apple.com/app-store", "play.google.com"
    }

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.client = client
        # Use snapshot suffix list for deterministic offline extraction
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
            # First try a quick HEAD request
            response = await client.head(target_url)
            # If server doesn't support HEAD (e.g. 405 Method Not Allowed), fall back to GET
            if response.status_code in (405, 501, 403):
                response = await client.get(target_url)

            final_url = str(response.url)
            if response.history:
                redirect_chain.extend([str(h.url) for h in response.history])
            redirect_chain.append(final_url)

            canonical = self.clean_domain(final_url)
            is_active = response.status_code < 500
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
        Tier 1 Knowledge Graph: Searches Wikidata and extracts Property P856 ("official website").
        Zero-key, public, highly authoritative entity mapping.
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
            for item in data[:3]:
                qid = item.get("id")
                label = item.get("label", "")
                desc = item.get("description", "")

                # Fetch entity claims
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

                # Score confidence
                score = 0.90
                if brand_token in domain.lower():
                    score += 0.08
                if any(w in desc.lower() for w in ("company", "corporation", "business", "technology", "enterprise", "firm", "financial")):
                    score += 0.02

                candidates.append(
                    CandidateEntity(
                        name=f"{label} ({desc})" if desc else label,
                        domain=domain,
                        description=f"Wikidata Knowledge Graph ({qid}) official website: {official_url}",
                        score=round(min(1.0, score), 2)
                    )
                )
        except Exception as e:
            logger.debug(f"Wikidata lookup failed: {e}")

        return candidates

    async def verify_candidate_domain(
        self,
        candidate_domain: str,
        target_name: str,
        brand_token: str,
        client: httpx.AsyncClient
    ) -> tuple[bool, float, Optional[str], Optional[str]]:
        """
        Bidirectional Ground-Truth Verification Guard:
        Probes https://{candidate_domain}, follows redirects, and validates whether
        the landing page title or JSON-LD declares the target company name.
        Returns: (is_verified, score, landed_domain, page_title)
        """
        target_url = f"https://{candidate_domain}"
        try:
            resp = await client.get(target_url, timeout=4.0)
            if resp.status_code >= 400:
                return False, 0.0, candidate_domain, None

            landed_domain = self.clean_domain(str(resp.url)) or candidate_domain
            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""

            meta_desc = ""
            desc_tag = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
            if desc_tag and desc_tag.get("content"):
                meta_desc = desc_tag["content"].strip()

            combined = (title + " " + meta_desc).lower()

            # Check for negative parking indicators
            NEGATIVE_PATTERNS = [
                "domain for sale", "buy this domain", "parked free", "website is under construction",
                "apache2 ubuntu default", "iis windows server", "domain parking", "404 not found"
            ]
            if any(neg in combined for neg in NEGATIVE_PATTERNS):
                return False, 0.0, landed_domain, title

            score = 0.0

            # 1. Exact standalone word match in title (e.g. "Square" in "Square: POS & Payments")
            if re.search(r"\b" + re.escape(brand_token) + r"\b", title.lower()):
                score += 0.50
            elif brand_token in title.lower():
                score += 0.30

            # 2. Target full name match in title or meta
            clean_name = re.sub(r"[^\w\s]", "", target_name.lower())
            if clean_name in combined:
                score += 0.30

            # 3. Domain token match
            if brand_token in landed_domain.lower():
                score += 0.30

            # 4. JSON-LD name verification
            scripts = soup.find_all("script", type=lambda t: t and "application/ld+json" in t.lower())
            for s in scripts:
                txt = s.get_text()
                if brand_token in txt.lower():
                    score += 0.20
                    break

            is_verified = score >= 0.50
            return is_verified, round(min(1.0, score), 2), landed_domain, title
        except Exception as e:
            logger.debug(f"Bidirectional verification probe failed for {candidate_domain}: {e}")
            return False, 0.0, candidate_domain, None

    async def search_name_to_domain(
        self,
        company_name: str,
        jurisdiction_hint: Optional[str] = None
    ) -> list[CandidateEntity]:
        """
        Executes the 3-Tier Cascading Waterfall to discover and verify candidate domains:
        Tier 1: Wikidata Knowledge Graph (P856 official website)
        Tier 2: Multi-Engine Search Waterfall (DuckDuckGo / SerpAPI)
        Tier 3: Domain Permutations + Bidirectional Verification Guard
        """
        clean_query, brand_token = CorporateNameCleanser.clean(company_name)
        candidates: list[CandidateEntity] = []

        client_to_close = None
        client = self.client

        if client is None:
            client_to_close = httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
                timeout=self.settings.HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
                verify=True
            )
            client = client_to_close

        try:
            seen_domains = set()

            # --- TIER 1: WIKIDATA KNOWLEDGE GRAPH ---
            wiki_candidates = await self.query_wikidata_official_website(clean_query, brand_token, client)
            for c in wiki_candidates:
                if c.domain not in seen_domains:
                    # Run Bidirectional Verification Guard on Wikidata domain
                    is_ver, ver_score, landed_domain, title = await self.verify_candidate_domain(
                        c.domain, company_name, brand_token, client
                    )
                    final_domain = landed_domain or c.domain
                    seen_domains.add(final_domain)
                    final_score = max(c.score, ver_score) if is_ver else c.score
                    candidates.append(
                        CandidateEntity(
                            name=title or c.name,
                            domain=final_domain,
                            description=c.description,
                            jurisdiction=jurisdiction_hint,
                            score=final_score
                        )
                    )

            # If Tier 1 found a high-assurance verified match (>= 0.90), return immediately!
            if candidates and candidates[0].score >= 0.90:
                logger.info(f"Tier 1 Wikidata resolved '{company_name}' -> {candidates[0].domain} (score: {candidates[0].score})")
                return candidates

            # --- TIER 2: SEARCH ENGINE WATERFALL ---
            query_parts = [f'"{clean_query}"', "official website OR headquarters"]
            if jurisdiction_hint:
                query_parts.append(jurisdiction_hint)
            query = " ".join(query_parts)

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

                        raw_domain = self.clean_domain(raw_href)
                        if not raw_domain or raw_domain in self.EXCLUDED_SEARCH_DOMAINS or raw_domain in seen_domains:
                            continue

                        # Execute Bidirectional Verification Guard
                        is_ver, ver_score, landed_domain, landed_title = await self.verify_candidate_domain(
                            raw_domain, company_name, brand_token, client
                        )
                        final_domain = landed_domain or raw_domain
                        if final_domain in seen_domains:
                            continue
                        seen_domains.add(final_domain)

                        snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                        candidates.append(
                            CandidateEntity(
                                name=landed_title or (title_elem.get_text(strip=True) if title_elem else final_domain),
                                domain=final_domain,
                                description=snippet,
                                jurisdiction=jurisdiction_hint,
                                score=ver_score if is_ver else 0.40
                            )
                        )

                        if len(candidates) >= 5:
                            break
            except Exception as e:
                logger.debug(f"DuckDuckGo search error: {e}")

            # --- TIER 3: PERMUTATIONS + BIDIRECTIONAL GUARD FALLBACK ---
            if not candidates:
                permutations = [
                    f"{brand_token}.com",
                    f"{re.sub(r'[^a-z0-9]', '', clean_query.lower())}.com",
                    f"{brand_token}.io",
                    f"{brand_token}.co",
                    f"get{brand_token}.com",
                    f"try{brand_token}.com",
                ]
                for perm in permutations:
                    if perm in seen_domains or perm in self.EXCLUDED_SEARCH_DOMAINS:
                        continue
                    is_ver, ver_score, landed_domain, landed_title = await self.verify_candidate_domain(
                        perm, company_name, brand_token, client
                    )
                    final_domain = landed_domain or perm
                    seen_domains.add(final_domain)
                    if is_ver:
                        candidates.append(
                            CandidateEntity(
                                name=landed_title or company_name,
                                domain=final_domain,
                                description=f"Verified direct domain permutation for '{company_name}'",
                                jurisdiction=jurisdiction_hint,
                                score=ver_score
                            )
                        )
                        break  # Found verified permutation!
                    else:
                        candidates.append(
                            CandidateEntity(
                                name=company_name,
                                domain=final_domain,
                                description=f"Unverified candidate permutation for '{company_name}'",
                                jurisdiction=jurisdiction_hint,
                                score=0.45
                            )
                        )

        finally:
            if client_to_close:
                await client_to_close.aclose()

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    async def resolve(self, request: EnrichmentRequest) -> CanonicalResolutionResult:
        """
        Coordinates full identity resolution and canonicalization.
        """
        # Case A: Website is explicitly provided
        if request.website:
            canonical, is_active, status_code, chain = await self.follow_redirects(request.website)
            return CanonicalResolutionResult(
                canonical_domain=canonical,
                original_input=request.website,
                active_web_presence=is_active,
                http_status=status_code,
                redirect_chain=chain,
                resolved_brand_name=request.name
            )

        # Case B: Only Company Name is provided
        if request.name:
            candidates = await self.search_name_to_domain(request.name, request.jurisdiction_hint)
            if not candidates:
                clean_query, brand_token = CorporateNameCleanser.clean(request.name)
                fallback_domain = f"{brand_token}.com"
                return CanonicalResolutionResult(
                    canonical_domain=fallback_domain,
                    original_input=request.name,
                    active_web_presence=False,
                    resolved_brand_name=clean_query or request.name,
                    candidates=[]
                )

            top = candidates[0]
            # Disambiguation required if top 2 candidates have strong competing scores
            disambiguation = (
                len(candidates) > 1
                and candidates[1].score >= 0.70
                and abs(top.score - candidates[1].score) < 0.12
                and top.domain != candidates[1].domain
            )

            return CanonicalResolutionResult(
                canonical_domain=top.domain,
                original_input=request.name,
                active_web_presence=top.score >= 0.50,
                candidates=candidates,
                disambiguation_required=disambiguation,
                resolved_brand_name=request.name
            )

        raise ValueError("Invalid request: No name or website provided.")
