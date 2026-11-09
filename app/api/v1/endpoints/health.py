from fastapi import APIRouter
from app.config import get_settings

router = APIRouter()
settings = get_settings()


@router.get(
    "/health",
    summary="Service Health Check",
    description="Returns service status, environment, and subsystem availability."
)
async def health_check():
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "environment": settings.ENVIRONMENT,
        "debug": settings.DEBUG,
        "features": {
            "sec_edgar_registry": "enabled (zero-key, rate-limited <=10 req/s)",
            "gleif_lei_registry": "enabled (zero-key)",
            "wikidata_knowledge_graph": "enabled (zero-key candidate source)",
            "duckduckgo_search": "enabled (zero-key candidate source)",
            "first_party_crawler": "enabled (zero-key)",
            "jsonld_extractor": "enabled (zero-key)",
            "brave_search": "enabled" if settings.BRAVE_SEARCH_API_KEY else "pluggable (inactive)",
            "companies_house_registry": "enabled" if settings.COMPANIES_HOUSE_API_KEY else "pluggable (inactive)",
            "opencorporates": "enabled (aggregator)" if settings.OPENCORPORATES_API_KEY else "pluggable (inactive)",
            "pdl": "enabled (vendor)" if settings.PDL_API_KEY else "pluggable (inactive)",
            "apollo": "enabled (vendor)" if settings.APOLLO_API_KEY else "pluggable (inactive)",
            "redis_cache": "connected" if settings.REDIS_URL else "bounded in-memory LRU fallback",
        }
    }
