from typing import Optional
from pydantic import BaseModel, Field, model_validator


class EnrichmentRequest(BaseModel):
    name: Optional[str] = Field(
        default=None,
        description="Company legal or trade name (e.g., 'Stripe, Inc.' or 'Stripe')"
    )
    website: Optional[str] = Field(
        default=None,
        description="Company website URL or domain (e.g., 'stripe.com' or 'https://stripe.com')"
    )
    jurisdiction_hint: Optional[str] = Field(
        default=None,
        description="Optional country/state ISO code or name to resolve homonyms (e.g., 'US', 'GB', 'DE', 'US-DE')"
    )
    registration_number: Optional[str] = Field(
        default=None,
        description="Exact company registration number in its jurisdiction (e.g., UK Companies House number)"
    )
    lei: Optional[str] = Field(
        default=None,
        description="20-character Global Legal Entity Identifier (ISO 17442 LEI)"
    )
    cik: Optional[str] = Field(
        default=None,
        description="US SEC Central Index Key (CIK)"
    )
    ticker: Optional[str] = Field(
        default=None,
        description="Listed stock ticker symbol (e.g., 'AAPL', 'NVDA')"
    )
    exchange: Optional[str] = Field(
        default=None,
        description="Stock exchange code (e.g., 'NASDAQ', 'NYSE')"
    )
    force_refresh: bool = Field(
        default=False,
        description="Bypass 30-day cache and perform a live audit pipeline execution"
    )

    @model_validator(mode="after")
    def validate_inputs(self):
        has_identifier = any([
            self.name,
            self.website,
            self.registration_number,
            self.lei,
            self.cik,
            self.ticker,
        ])
        if not has_identifier:
            raise ValueError(
                "At least one of 'name', 'website', 'registration_number', 'lei', 'cik', or 'ticker' must be provided."
            )
        return self


class BatchEnrichmentRequest(BaseModel):
    items: list[EnrichmentRequest] = Field(
        ...,
        max_length=50,
        description="List of companies to enrich concurrently (max 50 per batch)"
    )
