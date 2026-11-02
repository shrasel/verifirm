import pytest
import httpx

from app.models.request import EnrichmentRequest
from app.models.response import ConfidenceTier
from app.pipeline.orchestrator import EnrichmentPipelineOrchestrator

SAMPLE_HOMEPAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Acme Innovations</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Corporation",
        "name": "Acme",
        "legalName": "Acme Corporation Inc.",
        "foundingDate": "2015",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "100 Main St",
            "addressLocality": "Austin",
            "addressRegion": "TX",
            "postalCode": "78701",
            "addressCountry": "USA"
        },
        "telephone": "+1 512-555-0199",
        "email": "contact@acme.com"
    }
    </script>
</head>
<body>
    <h1>Welcome to Acme</h1>
</body>
</html>
"""

SAMPLE_TERMS = """
<!DOCTYPE html>
<html>
<body>
    <p>These terms govern your use of Acme services provided by Acme Corporation Inc.</p>
    <p>&copy; 2026 Acme Corporation Inc. All rights reserved.</p>
</body>
</html>
"""

SAMPLE_ABOUT = """
<!DOCTYPE html>
<html>
<body>
    <p>Acme was founded in 2015 by Jane Doe to build world-class tools.</p>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_end_to_end_pipeline_mocked():
    def mock_handler(request: httpx.Request):
        url = str(request.url)
        if "acme.com/terms" in url:
            return httpx.Response(200, text=SAMPLE_TERMS)
        elif "acme.com/about" in url:
            return httpx.Response(200, text=SAMPLE_ABOUT)
        elif "sec.gov" in url:
            # SEC ticker search response
            return httpx.Response(200, json={})
        elif "acme.com" in url:
            return httpx.Response(200, text=SAMPLE_HOMEPAGE)
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        orchestrator = EnrichmentPipelineOrchestrator(client=client)

        req = EnrichmentRequest(website="https://www.acme.com/products", force_refresh=True)
        res = await orchestrator.execute(req)

        assert res.status == "success"
        assert res.canonical_domain == "acme.com"
        assert "Acme Corporation Inc" in res.data.legal_name
        assert res.data.founded_year == 2015
        assert res.data.headquarters is not None
        assert res.data.headquarters.city == "Austin"
        assert res.data.headquarters.state == "TX"
        assert res.data.phone == "+1 512-555-0199"
        assert res.data.email == "contact@acme.com"

        # Check provenance audit trail
        assert "legal_name" in res.provenance
        assert res.provenance["legal_name"].confidence >= 0.90
        assert "founded_year" in res.provenance
        assert "phone" in res.provenance
        assert "email" in res.provenance

        # Check confidence tier
        assert res.confidence_score >= 0.90
        assert res.confidence_tier in (ConfidenceTier.HIGH_ASSURANCE, ConfidenceTier.MODERATE_ASSURANCE)
        assert res.metadata["cache_hit"] is False
