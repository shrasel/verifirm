import logging
from typing import Optional
import httpx

from app.config import get_settings
from app.fetchers.base import BaseFetcher, RawEvidenceBag

logger = logging.getLogger(__name__)


class PdlFetcher(BaseFetcher):
    """
    Tier C: People Data Labs (PDL) Commercial Adapter
    Pluggable: activates only if PDL_API_KEY is configured in environment.
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.client = client

    async def fetch(
        self,
        canonical_domain: str,
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        if evidence_bag is None:
            evidence_bag = RawEvidenceBag(canonical_domain=canonical_domain, brand_name=brand_name)

        if not self.settings.PDL_API_KEY:
            # Gracefully skip in zero-key environment
            return evidence_bag

        # Call PDL company enrichment if key present...
        return evidence_bag


class ApolloFetcher(BaseFetcher):
    """
    Tier C: Apollo.io Commercial Adapter
    Pluggable: activates only if APOLLO_API_KEY is configured in environment.
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.settings = get_settings()
        self.client = client

    async def fetch(
        self,
        canonical_domain: str,
        brand_name: Optional[str] = None,
        evidence_bag: Optional[RawEvidenceBag] = None
    ) -> RawEvidenceBag:
        if evidence_bag is None:
            evidence_bag = RawEvidenceBag(canonical_domain=canonical_domain, brand_name=brand_name)

        if not self.settings.APOLLO_API_KEY:
            # Gracefully skip in zero-key environment
            return evidence_bag

        # Call Apollo company enrichment if key present...
        return evidence_bag

