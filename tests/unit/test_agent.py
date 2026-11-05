import pytest
import httpx

from app.agent.context import AgentTrajectory
from app.agent.engine import AgentTrajectoryStore, CompanyEnrichmentAgent
from app.models.request import EnrichmentRequest
from app.models.response import ConfidenceTier

SAMPLE_HOMEPAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Stripe | Financial Infrastructure for the Internet</title>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Corporation",
      "name": "Stripe",
      "legalName": "Stripe, Inc.",
      "url": "https://stripe.com",
      "foundingDate": "2010",
      "telephone": "+1 888-926-2266",
      "email": "support@stripe.com",
      "address": {
        "@type": "PostalAddress",
        "streetAddress": "354 Oyster Point Blvd",
        "addressLocality": "South San Francisco",
        "addressRegion": "CA",
        "postalCode": "94080",
        "addressCountry": "USA"
      }
    }
    </script>
</head>
<body>
    <p>Founded in 2010 by Patrick Collison.</p>
    <p>&copy; 2026 Stripe Payments Company Inc. All rights reserved.</p>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_agent_react_cycle_mocked():
    def mock_handler(request: httpx.Request):
        url = str(request.url)
        if "sec.gov" in url:
            return httpx.Response(200, json={})
        return httpx.Response(200, text=SAMPLE_HOMEPAGE)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        agent = CompanyEnrichmentAgent(client=client)
        req = EnrichmentRequest(website="https://stripe.com", force_refresh=True)

        response, trajectory = await agent.run(req)

        # Assert response validity
        assert response.status == "success"
        assert response.canonical_domain == "stripe.com"
        assert "Stripe" in response.data.legal_name
        assert response.data.founded_year == 2010
        assert response.data.phone == "+1 888-926-2266"
        assert response.data.email == "support@stripe.com"
        assert response.confidence_score >= 0.85

        # Assert trajectory auditability
        assert trajectory.status == "completed"
        assert len(trajectory.steps) >= 5
        assert trajectory.final_confidence_score == response.confidence_score

        # Check trajectory step contents (ReAct auditability)
        step_tools = [s.tool_name for s in trajectory.steps]
        assert "resolve_identity" in step_tools
        assert "crawl_first_party_web" in step_tools
        assert "extract_structured_jsonld" in step_tools
        assert "reconcile_consensus" in step_tools
        assert "audit_grounding" in step_tools

        # Verify saved in Trajectory Store
        stored = AgentTrajectoryStore.get(trajectory.task_id)
        assert stored is not None
        assert stored.task_id == trajectory.task_id


def test_agent_trajectory_model():
    traj = AgentTrajectory(
        task_id="test-123",
        target_company="acme.com",
        goal="Test goal"
    )
    step = traj.add_step(
        thought="Thinking about step 1",
        tool_name="tool_a",
        tool_input={"query": "test"},
        observation_summary="Observed result"
    )
    assert step.step_number == 1
    assert len(traj.steps) == 1
    assert traj.steps[0].tool_name == "tool_a"
