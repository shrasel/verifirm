import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.fetchers.base import BaseFetcher, ExtractedEvidence, RawEvidenceBag, ScrapedPage
from app.fetchers.jsonld_extractor import JsonLdExtractor
from app.models.provenance import SourceType

logger = logging.getLogger(__name__)


class FirstPartyWebCrawler(BaseFetcher):
    """
    Tier A: First-Party Ground-Truth Crawler
    Crawls key paths: /, /about, /contact, /terms, /privacy.
    Extracts JSON-LD, copyright footers, founding declarations, and ground-truth text.
    """

    TARGET_PATHS = [
        ("/", ["/"]),
        ("/about", ["/about", "/about-us", "/our-story", "/company"]),
        ("/terms", ["/terms", "/terms-of-service", "/legal", "/tos"]),
        ("/privacy", ["/privacy", "/privacy-policy"]),
        ("/contact", ["/contact", "/contact-us", "/locations"]),
    ]

    COPYRIGHT_REGEX = re.compile(
        r"(?:copyright|©|\(c\))\s*(?:[12]\d{3}\s*[-–—]?\s*)?([12]\d{3})?\s*([A-Z0-9][A-Za-z0-9\s.,&'-]+?\b(?:(?:Corporation|Company|Corp\.?|Limited|Ltd\.?|LLC|Inc\.?|GmbH|B\.V\.|S\.A\.)\s*)+)",
        re.IGNORECASE
    )

    FOUNDED_REGEX = re.compile(
        r"(?:founded|established|started|incorporated)\s+(?:in|back\s+in)?\s*([12]\d{3})",
        re.IGNORECASE
    )

    EMAIL_REGEX = re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    )

    PHONE_REGEX = re.compile(
        r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}"
    )

    IGNORED_EMAIL_DOMAINS = {
        "example.com", "domain.com", "test.com", "yourcompany.com",
        "sentry.io", "w3.org", "schema.org", "github.com", "cloudflare.com"
    }

    IGNORED_EMAIL_EXTENSIONS = (
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".woff"
    )

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.client = client

    def clean_html_to_text(self, html: str) -> str:
        """
        Extracts clean textual corpus while stripping scripts, styles, and non-content noise.
        """
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        # Collapse excessive whitespace
        return re.sub(r"\s+", " ", text)

    def extract_copyright_legal_name(self, text: str) -> Optional[tuple[str, str]]:
        """
        Finds copyright lines like '© 2026 Stripe Payments Company Inc.'
        Returns (legal_name, matched_quote)
        """
        match = self.COPYRIGHT_REGEX.search(text)
        if match:
            quote = match.group(0).strip()
            # Group 2 has the captured company name
            candidate_name = match.group(2).strip()
            # Clean trailing punctuation
            candidate_name = re.sub(r"[,.]+$", "", candidate_name)
            if len(candidate_name) >= 3 and not candidate_name.lower().startswith("all rights"):
                return candidate_name, quote
        return None

    def extract_founded_year(self, text: str) -> Optional[tuple[int, str]]:
        """
        Searches text for 'Founded in 2010...'
        Returns (year, matched_quote)
        """
        match = self.FOUNDED_REGEX.search(text)
        if match:
            year_str = match.group(1)
            quote = match.group(0)
            try:
                year = int(year_str)
                if 1800 <= year <= 2030:
                    return year, quote
            except ValueError:
                pass
        return None

    def extract_contact_emails(self, soup: BeautifulSoup, text: str, canonical_domain: str) -> list[tuple[str, str]]:
        """
        Extracts verified contact emails from mailto links and page text.
        Prioritizes domain-matching emails and contact prefixes.
        """
        candidates: dict[str, str] = {}

        # 1. Look for explicit mailto links
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                raw_mail = href[7:].split("?")[0].strip().lower()
                if "@" in raw_mail and not any(raw_mail.endswith(ext) for ext in self.IGNORED_EMAIL_EXTENSIONS):
                    domain_part = raw_mail.split("@")[-1]
                    if domain_part not in self.IGNORED_EMAIL_DOMAINS:
                        candidates[raw_mail] = f'href="{href}"'

        # 2. Search clean text with regex
        for match in self.EMAIL_REGEX.finditer(text):
            email = match.group(0).lower().strip()
            if any(email.endswith(ext) for ext in self.IGNORED_EMAIL_EXTENSIONS):
                continue
            domain_part = email.split("@")[-1]
            if domain_part in self.IGNORED_EMAIL_DOMAINS:
                continue
            if email not in candidates:
                candidates[email] = match.group(0)

        # Score and rank candidates
        ranked = []
        for mail, quote in candidates.items():
            score = 1.0
            domain_part = mail.split("@")[-1]
            if canonical_domain in domain_part or domain_part in canonical_domain:
                score += 3.0
            prefix = mail.split("@")[0]
            if any(p in prefix for p in ["contact", "info", "support", "hello", "press", "legal", "sales", "help"]):
                score += 2.0
            ranked.append((score, mail, quote))

        ranked.sort(key=lambda x: x[0], reverse=True)
        return [(mail, quote) for _, mail, quote in ranked]

    def extract_phone_numbers(self, soup: BeautifulSoup, text: str) -> list[tuple[str, str]]:
        """
        Extracts verified phone numbers from tel: links and text.
        """
        candidates: dict[str, str] = {}

        # 1. Look for explicit tel: links
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("tel:"):
                raw_tel = href[4:].strip()
                digits = re.sub(r"\D", "", raw_tel)
                if 7 <= len(digits) <= 15:
                    candidates[raw_tel] = f'href="{href}"'

        # 2. Search clean text with regex
        for match in self.PHONE_REGEX.finditer(text):
            raw_phone = match.group(0).strip()
            digits = re.sub(r"\D", "", raw_phone)
            # Filter out dates or invalid lengths
            if 7 <= len(digits) <= 15:
                # Avoid common false positives like years "2020-2026"
                if re.match(r"^[12]\d{3}[-–—][12]\d{3}$", raw_phone):
                    continue
                # Avoid repetitive digits like "0000000"
                if len(set(digits)) > 2 and raw_phone not in candidates:
                    candidates[raw_phone] = raw_phone

        return list(candidates.items())

    async def _fetch_single_page(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        path_aliases: list[str]
    ) -> Optional[ScrapedPage]:
        """
        Attempts to fetch the primary path or its aliases until one returns HTTP 200.
        """
        for path in path_aliases:
            full_url = urljoin(base_url, path)
            try:
                response = await client.get(full_url)
                if response.status_code == 200 and len(response.text.strip()) > 50:
                    raw_html = response.text
                    clean_text = self.clean_html_to_text(raw_html)

                    soup = BeautifulSoup(raw_html, "html.parser")
                    title = soup.title.string.strip() if soup.title and soup.title.string else None

                    meta_desc = None
                    desc_tag = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
                    if desc_tag and desc_tag.get("content"):
                        meta_desc = desc_tag["content"].strip()

                    return ScrapedPage(
                        path=path,
                        url=str(response.url),
                        status_code=response.status_code,
                        raw_html=raw_html,
                        clean_text=clean_text,
                        title=title,
                        meta_description=meta_desc
                    )
            except Exception as e:
                logger.debug(f"Failed to fetch {full_url}: {e}")

        return None

    async def fetch(
        self,
        canonical_domain: str,
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        if evidence_bag is None:
            evidence_bag = RawEvidenceBag(canonical_domain=canonical_domain, brand_name=brand_name)

        base_url = f"https://{canonical_domain}"
        client_to_close = None
        client = self.client

        if client is None:
            client_to_close = httpx.AsyncClient(
                headers={
                    "User-Agent": self.settings.USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=self.settings.HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
                verify=False
            )
            client = client_to_close

        try:
            # Crawl all target page categories concurrently
            tasks = [
                self._fetch_single_page(client, base_url, aliases)
                for _, aliases in self.TARGET_PATHS
            ]
            pages = await asyncio.gather(*tasks)

            now_iso = datetime.now(timezone.utc).isoformat()

            for page in pages:
                if not page:
                    continue

                evidence_bag.add_page(page)

                # 1. Parse JSON-LD metadata
                JsonLdExtractor.extract_from_html(page.raw_html, page.url, evidence_bag)

                # 2. Extract Terms / Privacy legal names
                if any(k in page.path for k in ["/terms", "/privacy", "/legal"]):
                    legal_res = self.extract_copyright_legal_name(page.clean_text)
                    if legal_res:
                        name, quote = legal_res
                        evidence_bag.extracted_evidence.append(
                            ExtractedEvidence(
                                field="legal_name",
                                value=name,
                                source_name=page.url,
                                source_type=SourceType.FIRST_PARTY_TERMS.value,
                                confidence=0.96,
                                verbatim_quote=quote,
                                retrieved_at=now_iso
                            )
                        )

                # 3. Extract founding year from /about or /
                if any(k in page.path for k in ["/about", "/"]):
                    founded_res = self.extract_founded_year(page.clean_text)
                    if founded_res:
                        year, quote = founded_res
                        evidence_bag.extracted_evidence.append(
                            ExtractedEvidence(
                                field="founded_year",
                                value=year,
                                source_name=page.url,
                                source_type=SourceType.FIRST_PARTY_ABOUT.value,
                                confidence=0.94,
                                verbatim_quote=quote,
                                retrieved_at=now_iso
                            )
                        )

                # 4. Meta description as initial company summary
                if page.path == "/" and page.meta_description:
                    evidence_bag.extracted_evidence.append(
                        ExtractedEvidence(
                            field="description",
                            value=page.meta_description,
                            source_name=page.url,
                            source_type=SourceType.FIRST_PARTY_HOMEPAGE.value,
                            confidence=0.90,
                            verbatim_quote=page.meta_description[:200],
                            retrieved_at=now_iso
                        )
                    )

                # 5. Extract contact emails & phone numbers
                soup = BeautifulSoup(page.raw_html, "html.parser")
                page_src_type = (
                    SourceType.FIRST_PARTY_CONTACT.value if "contact" in page.path
                    else (SourceType.FIRST_PARTY_TERMS.value if any(t in page.path for t in ["terms", "privacy"])
                    else SourceType.FIRST_PARTY_HOMEPAGE.value)
                )

                emails = self.extract_contact_emails(soup, page.clean_text, canonical_domain)
                for mail, quote in emails[:2]:
                    evidence_bag.extracted_evidence.append(
                        ExtractedEvidence(
                            field="email",
                            value=mail,
                            source_name=page.url,
                            source_type=page_src_type,
                            confidence=0.96 if "contact" in page.path else 0.92,
                            verbatim_quote=quote,
                            retrieved_at=now_iso
                        )
                    )

                phones = self.extract_phone_numbers(soup, page.clean_text)
                for phone, quote in phones[:2]:
                    evidence_bag.extracted_evidence.append(
                        ExtractedEvidence(
                            field="phone",
                            value=phone,
                            source_name=page.url,
                            source_type=page_src_type,
                            confidence=0.96 if "contact" in page.path else 0.92,
                            verbatim_quote=quote,
                            retrieved_at=now_iso
                        )
                    )

        finally:
            if client_to_close:
                await client_to_close.aclose()

        return evidence_bag
