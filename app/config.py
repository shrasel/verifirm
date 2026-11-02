from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_NAME: str = "Company Verification & Enrichment Service"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # User-Agent for compliant crawling & SEC EDGAR
    USER_AGENT: str = "CompanyEnrichmentService/1.0 (auditable-pipeline; contact@example.com)"

    # Timeouts & Concurrency
    HTTP_TIMEOUT_SECONDS: float = 8.0
    MAX_REDIRECT_HOPS: int = 6
    CACHE_TTL_SECONDS: int = 60 * 60 * 24 * 30  # 30 days

    # Cache & Database URLs
    REDIS_URL: Optional[str] = None
    DATABASE_URL: Optional[str] = None

    # Optional Pluggable Provider Keys
    SERPAPI_API_KEY: Optional[str] = None
    OPENCORPORATES_API_KEY: Optional[str] = None
    PDL_API_KEY: Optional[str] = None
    APOLLO_API_KEY: Optional[str] = None
    CRUNCHBASE_API_KEY: Optional[str] = None

    # Optional LLM Keys
    GEMINI_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None


@lru_cache()
def get_settings() -> Settings:
    return Settings()

