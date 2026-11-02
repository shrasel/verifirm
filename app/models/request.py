from typing import Optional
from pydantic import BaseModel, Field, model_validator


class EnrichmentRequest(BaseModel):
    name: Optional[str] = Field(
        default=None,
        description="Company legal or trade name (e.g., 'Stripe' or 'Acme Corp')"
    )
    website: Optional[str] = Field(
        default=None,
        description="Company website URL or domain (e.g., 'stripe.com' or 'https://stripe.com')"
    )
    jurisdiction_hint: Optional[str] = Field(
        default=None,
        description="Optional country/state hint to resolve homonyms (e.g., 'US', 'DE', 'California')"
    )
    force_refresh: bool = Field(
        default=False,
        description="Bypass 30-day cache and perform a live audit pipeline execution"
    )

    @model_validator(mode="after")
    def validate_inputs(self):
        if not self.name and not self.website:
            raise ValueError("At least one of 'name' or 'website' must be provided.")
        return self


class BatchEnrichmentRequest(BaseModel):
    items: list[EnrichmentRequest] = Field(
        ...,
        max_length=50,
        description="List of companies to enrich concurrently (max 50 per batch)"
    )

