import pytest
import httpx
from app.models.request import EnrichmentRequest
from app.pipeline.stage1_canonical import CanonicalIdentityResolver


@pytest.fixture
def resolver():
    return CanonicalIdentityResolver()


def test_clean_domain(resolver):
    # Standard domain
    assert resolver.clean_domain("stripe.com") == "stripe.com"
    # Subdomains stripped to registered domain
    assert resolver.clean_domain("https://shop.stripe.com/checkout?ref=abc") == "stripe.com"
    assert resolver.clean_domain("http://www.blog.acme.co.uk/articles/123") == "acme.co.uk"
    assert resolver.clean_domain("app.tri-net.com") == "tri-net.com"
    assert resolver.clean_domain("GETPOCKET.COM") == "getpocket.com"


@pytest.mark.asyncio
async def test_follow_redirects_mocked():
    # Test redirect following with a mocked transport
    def handler(request: httpx.Request):
        url_str = str(request.url)
        if "zenefits.com" in url_str:
            return httpx.Response(301, headers={"Location": "https://www.trinet.com/"})
        elif "trinet.com" in url_str:
            return httpx.Response(200, text="TriNet Zenefits")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        resolver = CanonicalIdentityResolver(client=client)
        domain, is_active, status_code, chain = await resolver.follow_redirects("http://zenefits.com")

        assert domain == "trinet.com"
        assert is_active is True
        assert status_code == 200
        assert len(chain) >= 2


@pytest.mark.asyncio
async def test_resolve_with_website(resolver):
    def handler(request: httpx.Request):
        return httpx.Response(200, text="Stripe Homepage")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        resolver = CanonicalIdentityResolver(client=client)
        req = EnrichmentRequest(website="https://www.stripe.com/pricing")
        result = await resolver.resolve(req)

        assert result.canonical_domain == "stripe.com"
        assert result.active_web_presence is True
        assert result.disambiguation_required is False


def test_corporate_name_cleanser():
    from app.pipeline.stage1_canonical import CorporateNameCleanser

    query, brand = CorporateNameCleanser.clean("Meta Platforms, Inc.")
    assert query == "Meta Platforms"
    assert brand == "meta"

    query, brand = CorporateNameCleanser.clean("Square, Inc.")
    assert query == "Square"
    assert brand == "square"

    query, brand = CorporateNameCleanser.clean("Google DeepMind")
    assert query == "Google DeepMind"
    assert brand == "deepmind"


@pytest.mark.asyncio
async def test_bidirectional_verification_guard():
    def handler(request: httpx.Request):
        url = str(request.url)
        if "meta.com" in url:
            return httpx.Response(200, text="<html><head><title>Meta: Shop AI Glasses & VR</title></head></html>")
        elif "metacritic.com" in url:
            return httpx.Response(200, text="<html><head><title>Movie and Game Reviews</title></head></html>")
        elif "parked.com" in url:
            return httpx.Response(200, text="<html><head><title>Domain For Sale</title></head></html>")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        resolver = CanonicalIdentityResolver(client=client)

        # Meta candidate should pass verification
        is_ver, score, landed, title = await resolver.verify_candidate_domain("meta.com", "Meta Platforms", "meta", client)
        assert is_ver is True
        assert score >= 0.70
        assert landed == "meta.com"

        # Metacritic candidate should fail verification for Meta Platforms
        is_ver_bad, score_bad, _, _ = await resolver.verify_candidate_domain("metacritic.com", "Meta Platforms", "meta", client)
        assert is_ver_bad is False
        assert score_bad < 0.50

        # Parked domain should fail verification
        is_ver_parked, score_parked, _, _ = await resolver.verify_candidate_domain("parked.com", "Acme", "acme", client)
        assert is_ver_parked is False
        assert score_parked == 0.0


@pytest.mark.asyncio
async def test_resolve_with_company_name_wikidata_mocked():
    def handler(request: httpx.Request):
        url = str(request.url)
        if "wbsearchentities" in url:
            return httpx.Response(200, json={
                "search": [
                    {"id": "Q380", "label": "Meta Platforms", "description": "American technology company"}
                ]
            })
        elif "wbgetentities" in url:
            return httpx.Response(200, json={
                "entities": {
                    "Q380": {
                        "claims": {
                            "P856": [
                                {"mainsnak": {"datavalue": {"value": "https://www.meta.com/"}}}
                            ]
                        }
                    }
                }
            })
        elif "meta.com" in url:
            return httpx.Response(200, text="<html><head><title>Meta | AI & Metaverse</title></head></html>")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        resolver = CanonicalIdentityResolver(client=client)
        req = EnrichmentRequest(name="Meta Platforms, Inc.")
        result = await resolver.resolve(req)

        assert result.canonical_domain == "meta.com"
        assert result.active_web_presence is True
        assert len(result.candidates) >= 1
        assert result.candidates[0].score >= 0.80

