import logging
import re
from typing import Optional
from urllib.parse import urlparse
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


class CanonicalIdentityResolver:
    """
    Stage 1: Identity Resolution & Canonicalization
    - Follows 301/302 redirects across TLD/rebrand changes
    - Cleanses subdomains (shop., blog., app.) to root registered domain
    - Disambiguates company names via zero-key web search fallback
    """

    EXCLUDED_SEARCH_DOMAINS = {
        "wikipedia.org", "linkedin.com", "facebook.com", "twitter.com", "x.com",
        "instagram.com", "youtube.com", "crunchbase.com", "pitchbook.com",
        "bloomberg.com", "zoominfo.com", "glassdoor.com", "indeed.com",
        "dnb.com", "yellowpages.com", "bbb.org", "yelp.com", "trustpilot.com",
        "reddit.com", "github.com", "g2.com", "capterra.com", "sec.gov"
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
                verify=False
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
            # If HTTP connection fails (e.g. DNS or timeout), domain may still be syntactically valid
            fallback_canonical = self.clean_domain(target_url)
            return fallback_canonical, False, None, redirect_chain
        finally:
            if client_to_close:
                await client_to_close.aclose()

    async def search_name_to_domain(
        self,
        company_name: str,
        jurisdiction_hint: Optional[str] = None
    ) -> list[CandidateEntity]:
        """
        Free, zero-key search fallback using DuckDuckGo HTML search.
        Queries: "{company_name}" official website OR headquarters OR "about us"
        Filters out directories and extracts candidate official domains.
        """
        query_parts = [f'"{company_name}"', "official website OR headquarters OR \"about us\""]
        if jurisdiction_hint:
            query_parts.append(jurisdiction_hint)
        query = " ".join(query_parts)

        candidates: list[CandidateEntity] = []
        client_to_close = None
        client = self.client

        if client is None:
            client_to_close = httpx.AsyncClient(
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=self.settings.HTTP_TIMEOUT_SECONDS,
                follow_redirects=True
            )
            client = client_to_close

        try:
            url = "https://html.duckduckgo.com/html/"
            response = await client.post(url, data={"q": query})
            if response.status_code != 200:
                logger.warning(f"Search provider returned status {response.status_code}")
                return candidates

            soup = BeautifulSoup(response.text, "html.parser")
            results = soup.select(".web-result, .result")

            seen_domains = set()
            for r in results:
                title_elem = r.select_one(".result__title, .result__a")
                snippet_elem = r.select_one(".result__snippet")
                link_elem = r.select_one("a.result__url, a.result__a")

                if not link_elem:
                    continue

                raw_href = link_elem.get("href", "")
                # DuckDuckGo sometimes wraps urls in /l/?kh=-1&uddg=...
                if "uddg=" in raw_href:
                    match = re.search(r"uddg=([^&]+)", raw_href)
                    if match:
                        import urllib.parse
                        raw_href = urllib.parse.unquote(match.group(1))

                domain = self.clean_domain(raw_href)
                if not domain or domain in self.EXCLUDED_SEARCH_DOMAINS or domain in seen_domains:
                    continue

                seen_domains.add(domain)
                title = title_elem.get_text(strip=True) if title_elem else domain
                snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""

                # Score candidate relevance
                score = 0.5
                norm_name = re.sub(r"[^\w\s]", "", company_name.lower())
                norm_title = re.sub(r"[^\w\s]", "", title.lower())
                if norm_name in norm_title:
                    score += 0.3
                if norm_name in domain.lower():
                    score += 0.2

                candidates.append(
                    CandidateEntity(
                        name=title,
                        domain=domain,
                        description=snippet,
                        jurisdiction=jurisdiction_hint,
                        score=round(score, 2)
                    )
                )

                if len(candidates) >= 5:
                    break

        except Exception as e:
            logger.error(f"Error during zero-key search fallback: {e}")
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
                # Synthesize fallback domain from name if search is unavailable
                sanitized_slug = re.sub(r"[^\w]", "", request.name.lower())
                fallback_domain = f"{sanitized_slug}.com"
                return CanonicalResolutionResult(
                    canonical_domain=fallback_domain,
                    original_input=request.name,
                    active_web_presence=False,
                    resolved_brand_name=request.name,
                    candidates=[]
                )

            top = candidates[0]
            # If top score is dominant (e.g. >= 0.8), choose it; otherwise flag for disambiguation
            disambiguation = len(candidates) > 1 and (candidates[1].score >= 0.7 and abs(top.score - candidates[1].score) < 0.1)

            return CanonicalResolutionResult(
                canonical_domain=top.domain,
                original_input=request.name,
                active_web_presence=True,
                candidates=candidates,
                disambiguation_required=disambiguation,
                resolved_brand_name=request.name
            )

        raise ValueError("Invalid request: No name or website provided.")
