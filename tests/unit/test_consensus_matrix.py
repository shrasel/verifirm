import pytest
from app.consensus.confidence_scorer import ConsensusAndScoringEngine
from app.fetchers.base import ExtractedEvidence, RawEvidenceBag
from app.models.provenance import SourceType
from app.models.response import ConfidenceTier


@pytest.fixture
def engine():
    return ConsensusAndScoringEngine()


def test_registry_overrides_terms_and_commercial(engine):
    # Two conflicting legal names: one from Government Registry, one from Terms of Service
    claims = [
        ExtractedEvidence(
            field="legal_name",
            value="Stripe Payments Company Inc.",
            source_name="https://stripe.com/terms",
            source_type=SourceType.FIRST_PARTY_TERMS.value,
            confidence=0.96,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="legal_name",
            value="Stripe, Inc.",
            source_name="Delaware Division of Corporations",
            source_type=SourceType.GOVERNMENT_REGISTRY.value,
            confidence=1.00,
            filing_id="DE-4690025",
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="legal_name",
            value="Stripe Corporation",
            source_name="Commercial B2B Database",
            source_type=SourceType.COMMERCIAL_API.value,
            confidence=0.80,
            retrieved_at="2026-11-01T23:00:00Z"
        )
    ]

    res = engine.reconcile_field("legal_name", claims)
    assert res is not None
    # Government Registry must win over Terms of Service and Commercial API
    assert res.value == "Stripe, Inc."
    assert res.provenance.source_type == SourceType.GOVERNMENT_REGISTRY.value
    assert res.discrepancy is not None
    assert len(res.discrepancy.competing_values) == 3
    assert "Authority Matrix" in res.discrepancy.resolution_rationale


def test_agreement_yields_high_confidence(engine):
    # JSON-LD and About page both agree on founded_year 2010
    claims = [
        ExtractedEvidence(
            field="founded_year",
            value=2010,
            source_name="https://stripe.com",
            source_type=SourceType.FIRST_PARTY_JSONLD.value,
            confidence=0.96,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="founded_year",
            value=2010,
            source_name="https://stripe.com/about",
            source_type=SourceType.FIRST_PARTY_ABOUT.value,
            confidence=0.94,
            retrieved_at="2026-11-01T23:00:00Z"
        )
    ]

    res = engine.reconcile_field("founded_year", claims)
    assert res is not None
    assert res.value == 2010
    assert res.discrepancy is None
    # Consensus multiplier should be 1.0, so confidence >= 0.95
    assert res.provenance.confidence >= 0.95


def test_reconcile_all_ground_truth_cross_match(engine):
    # Cross-source verification (Registry + First-Party Terms match!)
    bag = RawEvidenceBag(canonical_domain="stripe.com")
    bag.extracted_evidence = [
        ExtractedEvidence(
            field="legal_name",
            value="Stripe, Inc.",
            source_name="SEC EDGAR",
            source_type=SourceType.GOVERNMENT_REGISTRY.value,
            confidence=1.0,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="legal_name",
            value="Stripe, Inc.",
            source_name="https://stripe.com/terms",
            source_type=SourceType.FIRST_PARTY_TERMS.value,
            confidence=0.96,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="founded_year",
            value=2010,
            source_name="SEC EDGAR",
            source_type=SourceType.GOVERNMENT_REGISTRY.value,
            confidence=1.0,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="founded_year",
            value=2010,
            source_name="https://stripe.com/about",
            source_type=SourceType.FIRST_PARTY_ABOUT.value,
            confidence=0.95,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="headquarters",
            value={"city": "South San Francisco", "state": "CA", "country": "USA"},
            source_name="SEC EDGAR",
            source_type=SourceType.GOVERNMENT_REGISTRY.value,
            confidence=0.98,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="headquarters",
            value={"city": "South San Francisco", "state": "CA", "country": "USA"},
            source_name="https://stripe.com/contact",
            source_type=SourceType.FIRST_PARTY_CONTACT.value,
            confidence=0.95,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="phone",
            value="+1-888-926-2266",
            source_name="SEC EDGAR",
            source_type=SourceType.GOVERNMENT_REGISTRY.value,
            confidence=0.98,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="phone",
            value="+1-888-926-2266",
            source_name="https://stripe.com/contact",
            source_type=SourceType.FIRST_PARTY_CONTACT.value,
            confidence=0.96,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="email",
            value="info@stripe.com",
            source_name="https://stripe.com/contact",
            source_type=SourceType.FIRST_PARTY_CONTACT.value,
            confidence=0.96,
            retrieved_at="2026-11-01T23:00:00Z"
        ),
        ExtractedEvidence(
            field="email",
            value="info@stripe.com",
            source_name="https://stripe.com",
            source_type=SourceType.FIRST_PARTY_JSONLD.value,
            confidence=0.98,
            retrieved_at="2026-11-01T23:00:00Z"
        )
    ]

    data, provenance, discrepancies, score, tier = engine.reconcile_all(bag, active_web_presence=True)

    # When Registry matches Terms of Service, consensus multiplier is 1.0 -> HIGH_ASSURANCE (>= 0.95)
    assert score >= 0.95
    assert tier == ConfidenceTier.HIGH_ASSURANCE
    assert data.legal_name == "Stripe, Inc."
    assert data.headquarters.city == "South San Francisco"
    assert data.phone == "+1-888-926-2266"
    assert data.email == "info@stripe.com"


def test_reconcile_single_credible_source_tier(engine):
    # Single credible source without opposing evidence -> MODERATE_ASSURANCE (0.70 - 0.94)
    bag = RawEvidenceBag(canonical_domain="acme.com")
    bag.extracted_evidence = [
        ExtractedEvidence(
            field="legal_name",
            value="Acme Corp",
            source_name="https://acme.com/terms",
            source_type=SourceType.FIRST_PARTY_TERMS.value,
            confidence=0.95,
            retrieved_at="2026-11-01T23:00:00Z"
        )
    ]
    data, provenance, discrepancies, score, tier = engine.reconcile_all(bag, active_web_presence=True)
    assert 0.70 <= score < 0.95
    assert tier == ConfidenceTier.MODERATE_ASSURANCE
