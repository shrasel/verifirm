import asyncio
import logging
from typing import Optional
import httpx
from fastapi import Request

from app.config import get_settings
from app.storage.cache import CacheManager

logger = logging.getLogger(__name__)

# Global concurrency guard: max 10 concurrent heavy enrichment operations
_CONCURRENCY_SEMAPHORE = asyncio.Semaphore(10)
_SHARED_CACHE = CacheManager()


def get_settings_dep():
    return get_settings()


def get_cache_manager() -> CacheManager:
    return _SHARED_CACHE


def get_concurrency_semaphore() -> asyncio.Semaphore:
    return _CONCURRENCY_SEMAPHORE


def get_http_client(request: Request) -> httpx.AsyncClient:
    """
    Retrieves the shared, connection-pooled HTTP client from FastAPI app state.
    Falls back to a standalone client if called outside app context.
    """
    if hasattr(request.app.state, "http_client") and request.app.state.http_client is not None:
        return request.app.state.http_client

    settings = get_settings()
    return httpx.AsyncClient(
        headers={"User-Agent": settings.USER_AGENT},
        timeout=settings.HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        verify=True
    )
