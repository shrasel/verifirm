import pytest
from app.models.company import CompanyData
from app.models.provenance import FieldProvenance, SourceType
from app.pipeline.stage4_grounding import GroundingAuditor


SAMPLE_CORPUS = """
--- PAGE: /about ---
Founded in 2010 by Patrick Collison and John Collison.
Stripe is a technology company that builds economic infrastructure for the internet.
--- PAGE: /terms ---
These terms are governed by Stripe Payments Company Inc., headquartered at 354 Oyster Point Blvd.
"""


def test_verify_grounding_pass():
    quote = "Founded in 2010 by Patrick Collison"
    assert GroundingAuditor.verify_grounding(quote, SAMPLE_CORPUS) is True


def test_verify_grounding_fail_hallucination():
    fake_quote = "Founded in 1999 by Elon Musk"
    assert GroundingAuditor.verify_grounding(fake_quote, SAMPLE_CORPUS) is False


def test_audit_provenance_penalizes_hallucination():
    data = CompanyData(legal_name="Stripe Payments Company Inc.", founded_year=1999)
    provenance = {
        "legal_name": FieldProvenance(
            source="https://stripe.com/terms",
            source_type=SourceType.FIRST_PARTY_TERMS.value,
            confidence=0.96,
            exact_quote="governed by Stripe Payments Company Inc.",
            verified_at="2026-11-01T23:50:00Z"
        ),
        "founded_year": FieldProvenance(
            source="https://stripe.com/about",
            source_type=SourceType.FIRST_PARTY_ABOUT.value,
            confidence=0.94,
            exact_quote="Founded in 1999 by Elon Musk",  # Hallucinated!
            verified_at="2026-11-01T23:50:00Z"
        )
    }

    failures = GroundingAuditor.audit_provenance(data, provenance, SAMPLE_CORPUS)
    assert "founded_year" in failures
    assert "legal_name" not in failures
    assert provenance["founded_year"].confidence < 0.50
    assert "[UNGROUNDED/AUDIT_FAILED]" in provenance["founded_year"].exact_quote
