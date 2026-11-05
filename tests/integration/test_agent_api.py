import pytest
from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_agent_enrich_and_trajectory_api():
    # 1. Dispatch Agent via POST /api/v1/agent/enrich
    response = client.post(
        "/api/v1/agent/enrich",
        json={"website": "stripe.com", "force_refresh": False}
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] in ("success", "needs_disambiguation")
    assert "response" in payload
    assert "trajectory" in payload

    # Verify Correlation ID header on response
    assert "x-correlation-id" in response.headers
    assert "x-response-time-ms" in response.headers

    task_id = payload["trajectory"]["task_id"]
    assert task_id is not None
    assert len(payload["trajectory"]["steps"]) >= 1

    # 2. Query Trajectory via GET /api/v1/agent/trajectory/{task_id}
    traj_resp = client.get(f"/api/v1/agent/trajectory/{task_id}")
    assert traj_resp.status_code == 200
    traj_data = traj_resp.json()
    assert traj_data["task_id"] == task_id
    assert traj_data["status"] in ("completed", "needs_disambiguation")


def test_agent_unified_error_envelope_validation():
    # Empty payload should trigger unified 422 error envelope
    response = client.post("/api/v1/agent/enrich", json={})
    assert response.status_code == 422
    data = response.json()
    assert data["status"] == "error"
    assert data["code"] == "VALIDATION_ERROR"
    assert "details" in data
