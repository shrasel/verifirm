from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.agent.context import AgentTrajectory
from app.agent.engine import AgentTrajectoryStore, CompanyEnrichmentAgent
from app.core.dependencies import get_cache_manager, get_concurrency_semaphore, get_http_client
from app.models.request import EnrichmentRequest
from app.models.response import EnrichmentResponse

router = APIRouter()


class AgentEnrichmentResult(BaseModel):
    status: str = Field(..., description="'success', 'needs_disambiguation', or 'error'")
    response: EnrichmentResponse = Field(..., description="The verified company payload and provenance")
    trajectory: AgentTrajectory = Field(..., description="The step-by-step reasoning and action trajectory")


@router.post(
    "/agent/enrich",
    response_model=AgentEnrichmentResult,
    status_code=status.HTTP_200_OK,
    summary="Autonomous Company Verification Agent",
    description="Dispatches the autonomous CompanyEnrichmentAgent using a ReAct reasoning cycle to investigate, verify, and audit company firmographics."
)
async def run_enrichment_agent(
    request: EnrichmentRequest,
    client=Depends(get_http_client),
    cache=Depends(get_cache_manager),
    semaphore=Depends(get_concurrency_semaphore)
) -> AgentEnrichmentResult:
    async with semaphore:
        agent = CompanyEnrichmentAgent(client=client, cache_manager=cache)
        try:
            response, trajectory = await agent.run(request)
            return AgentEnrichmentResult(
                status=response.status,
                response=response,
                trajectory=trajectory
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Agent execution failed: {str(e)}"
            )


@router.get(
    "/agent/trajectory/{task_id}",
    response_model=AgentTrajectory,
    summary="Retrieve Agent Reasoning Trajectory",
    description="Fetches the full step-by-step reasoning, tool invocations, and observation logs for a given agent task."
)
async def get_agent_trajectory(task_id: str) -> AgentTrajectory:
    trajectory = AgentTrajectoryStore.get(task_id)
    if not trajectory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No trajectory found for task ID '{task_id}'."
        )
    return trajectory
