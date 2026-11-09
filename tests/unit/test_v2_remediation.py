import pytest
import httpx

from app.consensus.confidence_scorer import ConsensusAndScoringEngine
from app.fetchers.base import ExtractedEvidence, RawEvidenceBag
from app.fetchers.registries.gleif import GleifFetcher
from app.fetchers.registries.opencorporates import OpenCorporatesFetcher
from app.fetchers.registries.sec_edgar import SecEdgarFetcher
from app.models.company import OperatingStatus
from app.models.provenance import SourceType
from app.models.request import EnrichmentRequest
from app.models.response import ConfidenceTier, MatchDecision
from app.pipeline.orchestrator import EnrichmentPipelineOrchestrator
from app.pipeline.stage1_canonical import CanonicalIdentityResolver


@pytest.mark.asyncio
async def test_no_guessed_brand_com_fallback_returns_unresolved():
    """
    Critique #1: When search and Wikidata find no verified candidates,
    never fabricate '{brand}.com'. Return MatchDecision.UNRESOLVED instead.
    """
    def mock_handler(request: httpx.Request):
        return httpx.Response(200, json={"search": []})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        resolver = CanonicalIdentityResolver(client=client)
        req = EnrichmentRequest(name="CompletelyNonExistentFictionalHoldingsXYZ Corp")
        res = await resolver.resolve(req)

        assert res.canonical_domain is None
        assert res.active_web_presence is False
        assert res.unresolved is True
        assert res.match_decision == MatchDecision.UNRESOLVED

        # Full orchestrator must also return status='unresolved', never guessing a .com domain
        orchestrator = EnrichmentPipelineOrchestrator(client=client)
        orch_res = await orchestrator.execute(req)
        assert orch_res.status == "unresolved"
        assert orch_res.match_decision == MatchDecision.UNRESOLVED
        assert orch_res.canonical_domain is None
        assert orch_res.confidence_tier == ConfidenceTier.UNRESOLVED
        assert orch_res.confidence_score == 0.0


@pytest.mark.asyncio
async def test_wikidata_unverified_candidate_does_not_early_return_or_automatch():
    """
    Critique #2: Wikidata P856 is a candidate generator, not proof.
    If the landing page check fails (e.g. 404 or unrelated title), it must NOT auto-match.
    """
    def mock_handler(request: httpx.Request):
        url = str(request.url)
        if "wbsearchentities" in url:
            return httpx.Response(200, json={
                "search": [{"id": "Q999", "label": "Phantom Systems", "description": "software company"}]
            })
        elif "wbgetentities" in url:
            return httpx.Response(200, json={
                "entities": {
                    "Q999": {
                        "claims": {
                            "P856": [{"mainsnak": {"datavalue": {"value": "https://phantom-broken-example.com"}}}]
                        }
                    }
                }
            })
        elif "phantom-broken-example.com" in url:
            # Landing page returns 404
            return httpx.Response(404, text="Not Found")
        return httpx.Response(200, text="<html></html>")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        resolver = CanonicalIdentityResolver(client=client)
        req = EnrichmentRequest(name="Phantom Systems Inc.")
        res = await resolver.resolve(req)

        assert res.canonical_domain is None
        assert res.match_decision == MatchDecision.UNRESOLVED
        assert len(res.candidates) == 1
        assert res.candidates[0].score < 0.45
        assert "landing_page_verification_failed" in res.candidates[0].contradicting_signals


@pytest.mark.asyncio
async def test_sec_edgar_preserves_casing_ranks_candidates_and_separates_addresses():
    """
    Critique #3:
    - Do not take first substring hit in SEC ticker file.
    - Do not corrupt acronyms with .title() (preserve 'NVIDIA CORP').
    - Separate business (operating HQ) from mailing (registered office) address.
    - Derive status from recent filings/listings instead of hardcoding 'Active' for deregistered filers.
    """
    def mock_handler(request: httpx.Request):
        url = str(request.url)
        if "company_tickers.json" in url:
            return httpx.Response(200, json={
                "0": {"cik_str": 111111, "ticker": "NVX", "title": "SUPER NVIDIA SUBSTRING TRAP INC"},
                "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
            })
        elif "CIK0001045810.json" in url:
            return httpx.Response(200, json={
                "cik": "0001045810",
                "name": "NVIDIA CORP",
                "sic": "3674",
                "sicDescription": "SEMICONDUCTORS & RELATED DEVICES",
                "stateOfIncorporation": "DE",
                "tickers": ["NVDA"],
                "exchanges": ["Nasdaq"],
                "formerNames": [{"name": "NVIDIA CORPORATION"}],
                "addresses": {
                    "business": {
                        "street1": "2788 SAN TOMAS EXPRESSWAY",
                        "city": "SANTA CLARA",
                        "stateOrCountry": "CA",
                        "zipCode": "95051",
                        "stateOrCountryDescription": "UNITED STATES"
                    },
                    "mailing": {
                        "street1": "251 LITTLE FALLS DRIVE",
                        "city": "WILMINGTON",
                        "stateOrCountry": "DE",
                        "zipCode": "19808",
                        "stateOrCountryDescription": "UNITED STATES"
                    }
                },
                "filings": {
                    "recent": {
                        "form": ["10-K"],
                        "filingDate": ["2026-02-25"]
                    }
                }
            })
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = SecEdgarFetcher(client=client)
        bag = RawEvidenceBag(canonical_domain="nvidia.com", brand_name="NVIDIA")
        await fetcher.fetch("nvidia.com", "NVIDIA", bag)

        legal_claims = [e for e in bag.extracted_evidence if e.field == "legal_name"]
        assert len(legal_claims) == 1
        # Exact casing preserved (not 'Nvidia Corp')
        assert legal_claims[0].value == "NVIDIA CORP"

        hq_claims = [e for e in bag.extracted_evidence if e.field == "headquarters"]
        reg_claims = [e for e in bag.extracted_evidence if e.field == "registered_office"]
        assert hq_claims[0].value["city"] == "SANTA CLARA"
        assert reg_claims[0].value["city"] == "WILMINGTON"


@pytest.mark.asyncio
async def test_opencorporates_classified_as_aggregator_below_primary_registry(monkeypatch):
    """
    Critique #4: OpenCorporates must be classified as REGISTRY_AGGREGATOR (confidence < 1.0),
    preserve casing, and yield to primary GOVERNMENT_REGISTRY sources.
    """
    fetcher = OpenCorporatesFetcher()
    monkeypatch.setattr(fetcher.settings, "OPENCORPORATES_API_KEY", "test-key")

    def mock_handler(request: httpx.Request):
        return httpx.Response(200, json={
            "results": {
                "companies": [
                    {
                        "company": {
                            "name": "MONZO BANK LIMITED",
                            "jurisdiction_code": "gb",
                            "company_number": "09446231",
                            "incorporation_date": "2015-02-23",
                            "opencorporates_url": "https://opencorporates.com/companies/gb/09446231"
                        }
                    }
                ]
            }
        })

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher.client = client
        bag = RawEvidenceBag(canonical_domain="monzo.com", brand_name="Monzo Bank")
        await fetcher.fetch("monzo.com", "Monzo Bank", bag)

        assert len(bag.extracted_evidence) >= 1
        legal_ev = [e for e in bag.extracted_evidence if e.field == "legal_name"][0]
        assert legal_ev.source_type == SourceType.REGISTRY_AGGREGATOR.value
        assert legal_ev.confidence <= 0.88
        assert legal_ev.value == "MONZO BANK LIMITED"


def test_source_family_independence_and_web_liveness_decoupled():
    """
    Critique #5:
    - Multiple claims from the same website belong to the same FIRST_PARTY_WEB family
      (independent_sources_count == 1).
    - Website liveness (active_web_presence=True) must NOT set status=Active when no registry status exists.
    - Claim confidence is respected in field scoring.
    """
    engine = ConsensusAndScoringEngine()
    bag = RawEvidenceBag(canonical_domain="acme.io", brand_name="Acme")
    bag.extracted_evidence = [
        ExtractedEvidence(
            field="legal_name",
            value="Acme Technologies LLC",
            source_name="https://acme.io/terms",
            source_type=SourceType.FIRST_PARTY_TERMS.value,
            confidence=0.95,
            verbatim_quote="Acme Technologies LLC",
            retrieved_at="2026-11-01T00:00:00Z"
        ),
        ExtractedEvidence(
            field="legal_name",
            value="Acme Technologies LLC",
            source_name="https://acme.io/",
            source_type=SourceType.FIRST_PARTY_JSONLD.value,
            confidence=0.94,
            retrieved_at="2026-11-01T00:00:00Z"
        ),
    ]
    bag.full_text_corpus = "These terms are by Acme Technologies LLC."

    data, provenance, _, score, tier = engine.reconcile_all(bag, active_web_presence=True)

    # Both claims are FIRST_PARTY_WEB -> independent_sources_count is 1, not 2
    assert provenance["legal_name"].independent_sources_count == 1
    # Website liveness does NOT invent OperatingStatus.ACTIVE
    assert data.status == OperatingStatus.UNKNOWN
    assert data.active_web_presence is True
    # Sparse 1-field record with single source family cannot reach HIGH_ASSURANCE
    assert tier == ConfidenceTier.MODERATE_ASSURANCE


@pytest.mark.asyncio
async def test_grounding_audit_recalculates_orchestrator_score():
    """
    Critique #5b: Grounding failures must recalculate and lower the composite confidence score.
    """
    engine = ConsensusAndScoringEngine()
    bag = RawEvidenceBag(canonical_domain="hallucinated.com", brand_name="Hallucinated")
    bag.extracted_evidence = [
        ExtractedEvidence(
            field="legal_name",
            value="Hallucinated Corp.",
            source_name="https://hallucinated.com/terms",
            source_type=SourceType.FIRST_PARTY_TERMS.value,
            confidence=0.96,
            verbatim_quote="This quote does not exist anywhere in the scraped corpus",
            retrieved_at="2026-11-01T00:00:00Z"
        )
    ]
    bag.full_text_corpus = "--- PAGE: /terms ---\nWelcome to our website."

    data, provenance, _, score, tier = engine.reconcile_all(bag, active_web_presence=True)
    assert provenance["legal_name"].confidence < 0.50
    assert score < 0.50
    assert tier == ConfidenceTier.LOW_ASSURANCE


@pytest.mark.asyncio
async def test_gleif_and_entity_only_resolution_without_website():
    """
    Entity-first resolution: Resolve a company by LEI or name via GLEIF even when no website exists.
    """
    def mock_handler(request: httpx.Request):
        url = str(request.url)
        if "api.gleif.org" in url:
            return httpx.Response(200, json={
                "data": {
                    "id": "5493006MHB84DD0ZWV18",
                    "attributes": {
                        "lei": "5493006MHB84DD0ZWV18",
                        "entity": {
                            "legalName": {"name": "SIEMENS AKTIENGESELLSCHAFT"},
                            "jurisdiction": "DE",
                            "registeredAs": "HRB 6684",
                            "status": "ACTIVE",
                            "legalAddress": {
                                "addressLines": ["Werner-von-Siemens-Straße 1"],
                                "city": "München",
                                "postalCode": "80333",
                                "country": "DE"
                            },
                            "headquartersAddress": {
                                "addressLines": ["Werner-von-Siemens-Straße 1"],
                                "city": "München",
                                "postalCode": "80333",
                                "country": "DE"
                            }
                        },
                        "registration": {"lastUpdateDate": "2026-01-15T00:00:00Z"}
                    }
                }
            })
        return httpx.Response(200, json={"search": []})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        orchestrator = EnrichmentPipelineOrchestrator(client=client)
        req = EnrichmentRequest(lei="5493006MHB84DD0ZWV18", jurisdiction_hint="DE")
        res = await orchestrator.execute(req)

        assert res.status == "success"
        assert res.match_decision == MatchDecision.AUTO_MATCH
        assert res.canonical_domain is None
        assert res.data.legal_name == "SIEMENS AKTIENGESELLSCHAFT"
        assert res.data.status == OperatingStatus.ACTIVE
        assert res.data.lei == "5493006MHB84DD0ZWV18"
        assert res.data.legal_entity is not None
        assert res.data.legal_entity.registered_name == "SIEMENS AKTIENGESELLSCHAFT"
        assert res.data.legal_entity.registration_number == "HRB 6684"
        assert res.data.registered_office.city == "München"

