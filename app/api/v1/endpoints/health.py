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
            "sec_edgar_registry": "enabled (zero-key)",
            "duckduckgo_search": "enabled (zero-key)",
            "first_party_crawler": "enabled (zero-key)",
            "jsonld_extractor": "enabled (zero-key)",
            "opencorporates": "enabled" if settings.OPENCORPORATES_API_KEY else "pluggable (inactive)",
            "pdl": "enabled" if settings.PDL_API_KEY else "pluggable (inactive)",
            "apollo": "enabled" if settings.APOLLO_API_KEY else "pluggable (inactive)",
            "redis_cache": "connected" if settings.REDIS_URL else "in-memory TTL fallback",
        }
    }
