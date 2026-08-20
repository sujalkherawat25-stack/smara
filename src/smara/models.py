from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

TaskStatus = Literal["queued", "running", "waiting_approval", "cancelling", "completed", "failed", "cancelled"]


class TaskStepInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    depends_on: list[int] = Field(default_factory=list, max_length=30)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20_000)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    requires_approval: bool = True
    steps: list[TaskStepInput] = Field(default_factory=lambda: [TaskStepInput(name="execute_task")], min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_step_graph(self):
        for index, step in enumerate(self.steps):
            if len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError("a step cannot depend on the same step twice")
            if any(parent < 0 or parent >= index for parent in step.depends_on):
                raise ValueError("steps may only depend on earlier steps")
        return self


class TaskView(BaseModel):
    id: str
    account_id: str
    workspace_id: str
    title: str
    objective: str
    status: TaskStatus
    requires_approval: bool
    created_at: datetime
    updated_at: datetime


class ApprovalDecision(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=2_000)


class ResearchTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=5, max_length=20_000)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    sources: list[AnyHttpUrl] = Field(min_length=1, max_length=12)


class EvidenceView(BaseModel):
    id: str
    task_id: str
    url: str
    title: str | None = None
    status: Literal["pending", "fetched", "verified", "failed", "blocked"]
    retrieved_at: datetime | None = None
    content_sha256: str | None = None
    excerpt: str | None = None
    claim: str | None = None
    confidence: float | None = None
    citation_label: str | None = None
    error: str | None = None


class ArtifactView(BaseModel):
    id: str
    task_id: str
    kind: str
    name: str
    uri: str
    sha256: str | None = None
    content: str | None = None
    created_at: datetime
