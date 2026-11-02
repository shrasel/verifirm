import asyncio
import logging
from typing import Optional
import httpx

from app.fetchers.base import RawEvidenceBag
from app.fetchers.commercial.adapters import ApolloFetcher, PdlFetcher
from app.fetchers.registries.opencorporates import OpenCorporatesFetcher
from app.fetchers.registries.sec_edgar import SecEdgarFetcher
from app.fetchers.web_crawler import FirstPartyWebCrawler

logger = logging.getLogger(__name__)


class Stage2DataFetcher:
    """
    Stage 2: Parallel Multi-Tier Data Fetching Orchestrator
    Dispatches Tier A (Crawler/JSON-LD), Tier B (Registries), and Tier C (B2B Waterfall)
    concurrently with bounded timeout.
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.client = client
        self.web_crawler = FirstPartyWebCrawler(client=client)
        self.sec_fetcher = SecEdgarFetcher(client=client)
        self.opencorporates_fetcher = OpenCorporatesFetcher(client=client)
        self.pdl_fetcher = PdlFetcher(client=client)
        self.apollo_fetcher = ApolloFetcher(client=client)

    async def fetch_all(
        self,
        canonical_domain: str,
        brand_name: Optional[str] = None
    ) -> RawEvidenceBag:
        bag = RawEvidenceBag(canonical_domain=canonical_domain, brand_name=brand_name)

        # Run Tier A and Tier B in parallel
        tasks = [
            self.web_crawler.fetch(canonical_domain, brand_name, bag),
            self.sec_fetcher.fetch(canonical_domain, brand_name, bag),
            self.opencorporates_fetcher.fetch(canonical_domain, brand_name, bag),
            self.pdl_fetcher.fetch(canonical_domain, brand_name, bag),
            self.apollo_fetcher.fetch(canonical_domain, brand_name, bag),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.warning(f"Fetcher task {i} encountered an error: {res}")

        logger.info(
            f"Stage 2 Fetch Complete for {canonical_domain}: "
            f"{len(bag.pages)} pages, {len(bag.jsonld_entities)} JSON-LD blocks, "
            f"{len(bag.extracted_evidence)} raw evidence claims."
        )
        return bag

