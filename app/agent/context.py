import time
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field


class AgentStep(BaseModel):
    step_number: int = Field(..., description="Sequential step index")
    thought: str = Field(..., description="Agent reasoning before acting")
    tool_name: str = Field(..., description="Invoked tool name")
    tool_input: dict[str, Any] = Field(default_factory=dict, description="Input parameters passed to the tool")
    observation_summary: str = Field(..., description="Concise summary of findings observed from tool execution")
    duration_ms: float = Field(0.0, description="Execution time for this step in milliseconds")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO timestamp of step execution"
    )


class AgentTrajectory(BaseModel):
    task_id: str = Field(..., description="Unique UUID tracking this agent execution")
    target_company: str = Field(..., description="Company name or domain being investigated")
    goal: str = Field(..., description="Mission goal and assurance threshold")
    status: str = Field("in_progress", description="'in_progress', 'completed', 'failed', or 'needs_disambiguation'")
    steps: list[AgentStep] = Field(default_factory=list, description="Ordered audit log of thoughts and actions")
    final_confidence_score: float = Field(0.0, description="Final composite confidence score achieved")
    total_duration_ms: float = Field(0.0, description="Total agent execution duration in milliseconds")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Timestamp when agent was dispatched"
    )
    completed_at: Optional[str] = Field(default=None, description="Timestamp when agent finished")

    def add_step(
        self,
        thought: str,
        tool_name: str,
        tool_input: dict[str, Any],
        observation_summary: str,
        duration_ms: float = 0.0
    ) -> AgentStep:
        step = AgentStep(
            step_number=len(self.steps) + 1,
            thought=thought,
            tool_name=tool_name,
            tool_input=tool_input,
            observation_summary=observation_summary,
            duration_ms=round(duration_ms, 2)
        )
        self.steps.append(step)
        return step
