from typing import Any, Optional
from app.models.provenance import SourceType


class AuthorityMatrix:
    """
    Deterministically ranks data source authority per field.
    Higher weight = higher authority when resolving discrepancies.
    """

    PRIORITY_WEIGHTS: dict[str, dict[str, float]] = {
        "legal_name": {
            SourceType.GOVERNMENT_REGISTRY.value: 1.00,
            SourceType.FIRST_PARTY_TERMS.value: 0.96,
            SourceType.FIRST_PARTY_JSONLD.value: 0.94,
            SourceType.FIRST_PARTY_ABOUT.value: 0.85,
            SourceType.COMMERCIAL_API.value: 0.80,
            SourceType.SEARCH_INFERENCE.value: 0.70,
        },
        "status": {
            SourceType.GOVERNMENT_REGISTRY.value: 1.00,
            SourceType.FIRST_PARTY_TERMS.value: 0.95,
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.90,
            SourceType.COMMERCIAL_API.value: 0.80,
        },
        "founded_year": {
            SourceType.GOVERNMENT_REGISTRY.value: 1.00,
            SourceType.FIRST_PARTY_JSONLD.value: 0.96,
            SourceType.FIRST_PARTY_ABOUT.value: 0.94,
            SourceType.COMMERCIAL_API.value: 0.80,
            SourceType.SEARCH_INFERENCE.value: 0.70,
        },
        "headquarters": {
            SourceType.GOVERNMENT_REGISTRY.value: 0.98,
            SourceType.FIRST_PARTY_JSONLD.value: 0.96,
            SourceType.FIRST_PARTY_TERMS.value: 0.95,
            SourceType.FIRST_PARTY_CONTACT.value: 0.92,
            SourceType.COMMERCIAL_API.value: 0.80,
        },
        "employee_range": {
            SourceType.FIRST_PARTY_JSONLD.value: 0.92,
            SourceType.FIRST_PARTY_ABOUT.value: 0.90,
            SourceType.COMMERCIAL_API.value: 0.85,
        },
        "industry": {
            SourceType.GOVERNMENT_REGISTRY.value: 1.00,
            SourceType.FIRST_PARTY_JSONLD.value: 0.92,
            SourceType.COMMERCIAL_API.value: 0.80,
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.75,
        },
        "brand_name": {
            SourceType.FIRST_PARTY_JSONLD.value: 0.96,
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.95,
            SourceType.SEARCH_INFERENCE.value: 0.85,
            SourceType.GOVERNMENT_REGISTRY.value: 0.80,
        },
        "description": {
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.95,
            SourceType.FIRST_PARTY_JSONLD.value: 0.92,
            SourceType.COMMERCIAL_API.value: 0.80,
        },
        "phone": {
            SourceType.FIRST_PARTY_JSONLD.value: 0.98,
            SourceType.FIRST_PARTY_CONTACT.value: 0.96,
            SourceType.GOVERNMENT_REGISTRY.value: 0.95,
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.92,
            SourceType.FIRST_PARTY_TERMS.value: 0.90,
            SourceType.COMMERCIAL_API.value: 0.80,
        },
        "email": {
            SourceType.FIRST_PARTY_JSONLD.value: 0.98,
            SourceType.FIRST_PARTY_CONTACT.value: 0.96,
            SourceType.FIRST_PARTY_TERMS.value: 0.93,
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.92,
            SourceType.COMMERCIAL_API.value: 0.80,
        }
    }

    # Importance weights for calculating overall record confidence
    FIELD_WEIGHTS: dict[str, float] = {
        "legal_name": 0.20,
        "status": 0.15,
        "headquarters": 0.15,
        "founded_year": 0.15,
        "phone": 0.08,
        "email": 0.08,
        "industry": 0.09,
        "employee_range": 0.04,
        "brand_name": 0.03,
        "description": 0.03,
    }

    @classmethod
    def get_source_weight(cls, field: str, source_type: str) -> float:
        field_weights = cls.PRIORITY_WEIGHTS.get(field, {})
        return field_weights.get(source_type, 0.70)

