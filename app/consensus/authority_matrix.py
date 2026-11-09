from app.models.provenance import SourceType


class AuthorityMatrix:
    """
    Deterministically ranks data source authority per field.
    Separates primary statutory registries (GOVERNMENT_REGISTRY) from secondary
    aggregators (REGISTRY_AGGREGATOR) and commercial vendors (COMMERCIAL_API).
    """

    PRIORITY_WEIGHTS: dict[str, dict[str, float]] = {
        "legal_name": {
            SourceType.GOVERNMENT_REGISTRY.value: 1.00,
            SourceType.FIRST_PARTY_TERMS.value: 0.96,
            SourceType.FIRST_PARTY_JSONLD.value: 0.94,
            SourceType.REGISTRY_AGGREGATOR.value: 0.88,
            SourceType.PUBLIC_KNOWLEDGE_GRAPH.value: 0.85,
            SourceType.FIRST_PARTY_ABOUT.value: 0.85,
            SourceType.COMMERCIAL_API.value: 0.78,
            SourceType.SEARCH_INFERENCE.value: 0.65,
        },
        "status": {
            SourceType.GOVERNMENT_REGISTRY.value: 1.00,
            SourceType.REGISTRY_AGGREGATOR.value: 0.86,
            SourceType.FIRST_PARTY_TERMS.value: 0.85,
            SourceType.COMMERCIAL_API.value: 0.75,
        },
        "founded_year": {
            SourceType.GOVERNMENT_REGISTRY.value: 1.00,
            SourceType.FIRST_PARTY_JSONLD.value: 0.96,
            SourceType.FIRST_PARTY_ABOUT.value: 0.94,
            SourceType.REGISTRY_AGGREGATOR.value: 0.88,
            SourceType.PUBLIC_KNOWLEDGE_GRAPH.value: 0.85,
            SourceType.COMMERCIAL_API.value: 0.78,
            SourceType.SEARCH_INFERENCE.value: 0.65,
        },
        "headquarters": {
            SourceType.GOVERNMENT_REGISTRY.value: 0.98,
            SourceType.FIRST_PARTY_JSONLD.value: 0.96,
            SourceType.FIRST_PARTY_TERMS.value: 0.95,
            SourceType.FIRST_PARTY_CONTACT.value: 0.92,
            SourceType.REGISTRY_AGGREGATOR.value: 0.85,
            SourceType.COMMERCIAL_API.value: 0.78,
        },
        "registered_office": {
            SourceType.GOVERNMENT_REGISTRY.value: 1.00,
            SourceType.REGISTRY_AGGREGATOR.value: 0.88,
            SourceType.FIRST_PARTY_TERMS.value: 0.90,
        },
        "employee_range": {
            SourceType.FIRST_PARTY_JSONLD.value: 0.92,
            SourceType.FIRST_PARTY_ABOUT.value: 0.90,
            SourceType.COMMERCIAL_API.value: 0.78,
        },
        "industry": {
            SourceType.GOVERNMENT_REGISTRY.value: 1.00,
            SourceType.FIRST_PARTY_JSONLD.value: 0.92,
            SourceType.COMMERCIAL_API.value: 0.78,
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.75,
        },
        "brand_name": {
            SourceType.FIRST_PARTY_JSONLD.value: 0.96,
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.95,
            SourceType.PUBLIC_KNOWLEDGE_GRAPH.value: 0.88,
            SourceType.SEARCH_INFERENCE.value: 0.80,
            SourceType.GOVERNMENT_REGISTRY.value: 0.80,
        },
        "description": {
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.95,
            SourceType.FIRST_PARTY_JSONLD.value: 0.92,
            SourceType.COMMERCIAL_API.value: 0.78,
        },
        "phone": {
            SourceType.FIRST_PARTY_JSONLD.value: 0.98,
            SourceType.FIRST_PARTY_CONTACT.value: 0.96,
            SourceType.GOVERNMENT_REGISTRY.value: 0.96,
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.92,
            SourceType.FIRST_PARTY_TERMS.value: 0.90,
            SourceType.COMMERCIAL_API.value: 0.78,
        },
        "email": {
            SourceType.FIRST_PARTY_JSONLD.value: 0.98,
            SourceType.FIRST_PARTY_CONTACT.value: 0.96,
            SourceType.FIRST_PARTY_TERMS.value: 0.93,
            SourceType.FIRST_PARTY_HOMEPAGE.value: 0.92,
            SourceType.COMMERCIAL_API.value: 0.78,
        }
    }

    # Importance weights for calculating overall record confidence
    FIELD_WEIGHTS: dict[str, float] = {
        "legal_name": 0.22,
        "status": 0.12,
        "headquarters": 0.16,
        "registered_office": 0.06,
        "founded_year": 0.14,
        "phone": 0.08,
        "email": 0.08,
        "industry": 0.06,
        "employee_range": 0.03,
        "brand_name": 0.03,
        "description": 0.02,
    }

    @classmethod
    def get_source_weight(cls, field: str, source_type: str) -> float:
        field_weights = cls.PRIORITY_WEIGHTS.get(field, {})
        return field_weights.get(source_type, 0.65)
