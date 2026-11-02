import pytest
import httpx
from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "sec_edgar_registry" in data["features"]


def test_enrich_validation_empty():
    # Empty request should fail with 422 Unprocessable Entity
    response = client.post("/api/v1/enrich", json={})
    assert response.status_code == 422


def test_enrich_endpoint_cached_or_live():
    # We can post an enrichment request
    response = client.post(
        "/api/v1/enrich",
        json={"website": "stripe.com", "force_refresh": False}
    )
    # The endpoint should return a 200 response with structured payload
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in ("success", "needs_disambiguation")
    assert "data" in payload
    assert "provenance" in payload
    assert "confidence_score" in payload
    assert payload["canonical_domain"] == "stripe.com"


def test_audit_endpoint_retrieval():
    # First enrich
    client.post("/api/v1/enrich", json={"website": "stripe.com"})
    # Then query audit
    response = client.get("/api/v1/enrich/stripe.com/audit")
    assert response.status_code == 200
    payload = response.json()
    assert payload["canonical_domain"] == "stripe.com"
    assert "provenance" in payload

