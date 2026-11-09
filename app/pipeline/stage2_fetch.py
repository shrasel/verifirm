import asyncio
import logging
from typing import Optional
import httpx

from app.fetchers.base import RawEvidenceBag
from app.fetchers.commercial.adapters import ApolloFetcher, PdlFetcher
from app.fetchers.registries.companies_house import CompaniesHouseFetcher
from app.fetchers.registries.gleif import GleifFetcher
from app.fetchers.registries.opencorporates import OpenCorporatesFetcher
from app.fetchers.registries.sec_edgar import SecEdgarFetcher
from app.fetchers.web_crawler import FirstPartyWebCrawler

logger = logging.getLogger(__name__)


class Stage2DataFetcher:
    """
    Stage 2: Parallel Multi-Tier Data Fetching Orchestrator
    Dispatches Tier A (Crawler/JSON-LD), Tier B (SEC EDGAR, GLEIF LEI, UK Companies House, OpenCorporates),
    and Tier C (B2B Commercial Adapters) concurrently with bounded timeout.
    Supports both domain-first and entity-first (no-website) resolution.
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.client = client
        self.web_crawler = FirstPartyWebCrawler(client=client)
        self.sec_fetcher = SecEdgarFetcher(client=client)
        self.gleif_fetcher = GleifFetcher(client=client)
        self.companies_house_fetcher = CompaniesHouseFetcher(client=client)
        self.opencorporates_fetcher = OpenCorporatesFetcher(client=client)
        self.pdl_fetcher = PdlFetcher(client=client)
        self.apollo_fetcher = ApolloFetcher(client=client)

    async def fetch_all(
        self,
        canonical_domain: Optional[str],
        brand_name: Optional[str] = None,
        jurisdiction_hint: Optional[str] = None,
        cik: Optional[str] = None,
        lei: Optional[str] = None,
        registration_number: Optional[str] = None,
        ticker: Optional[str] = None,
        exchange: Optional[str] = None,
        http_status: Optional[int] = None,
    ) -> RawEvidenceBag:
        bag = RawEvidenceBag(
            canonical_domain=canonical_domain,
            brand_name=brand_name,
            jurisdiction_hint=jurisdiction_hint,
            cik=cik,
            lei=lei,
            registration_number=registration_number,
            ticker=ticker,
            exchange=exchange,
            http_status=http_status,
        )

        tasks = [
            self.sec_fetcher.fetch(canonical_domain, brand_name, bag),
            self.gleif_fetcher.fetch(canonical_domain, brand_name, bag),
            self.companies_house_fetcher.fetch(canonical_domain, brand_name, bag),
            self.opencorporates_fetcher.fetch(canonical_domain, brand_name, bag),
            self.pdl_fetcher.fetch(canonical_domain, brand_name, bag),
            self.apollo_fetcher.fetch(canonical_domain, brand_name, bag),
        ]
        if canonical_domain:
            tasks.insert(0, self.web_crawler.fetch(canonical_domain, brand_name, bag))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.warning(f"Fetcher task {i} encountered an error: {res}")

        logger.info(
            f"Stage 2 Fetch Complete for domain='{canonical_domain}' brand='{brand_name}': "
            f"{len(bag.pages)} pages, {len(bag.jsonld_entities)} JSON-LD blocks, "
            f"{len(bag.registry_filings)} registry filings, "
            f"{len(bag.extracted_evidence)} raw evidence claims."
        )
        return bag
