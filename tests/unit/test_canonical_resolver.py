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
