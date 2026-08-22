from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

TaskStatus = Literal["queued", "running", "waiting_approval", "cancelling", "completed", "failed", "cancelled"]


class TaskStepInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    depends_on: list[int] = Field(default_factory=list, max_length=30)
    executor_kind: Literal["hosted", "desktop", "sandbox"] = "hosted"
    required_capability: str | None = Field(default=None, min_length=1, max_length=80)
    executor_payload: dict[str, Any] = Field(default_factory=dict, max_length=30)


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


class ChatRequest(BaseModel):
    """A short conversational turn; durable work belongs in TaskCreate."""
    message: str = Field(min_length=1, max_length=20_000)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=160)


class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    memory_used: bool
    model: str | None = None


class CliPairingStart(BaseModel):
    name: str = Field(default="Smara CLI", min_length=1, max_length=120)


class CliPairingExchange(BaseModel):
    code: str = Field(min_length=12, max_length=160)


class ApprovalDecision(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=2_000)


class AccountDeletionRequest(BaseModel):
    """Deliberate confirmation; no account data is deleted by accident."""
    confirm_account_id: str = Field(min_length=1, max_length=128)


class ResearchTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=5, max_length=20_000)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    sources: list[AnyHttpUrl] = Field(default_factory=list, max_length=12)


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


class ExecutorPairingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    capabilities: list[str] = Field(min_length=1, max_length=20)


class ExecutorPairRequest(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9]{8}$")


class ExecutorHeartbeat(BaseModel):
    capabilities: list[str] = Field(default_factory=list, max_length=20)


class ExecutorComplete(BaseModel):
    result: str = Field(min_length=1, max_length=20_000)


class ExecutorFailure(BaseModel):
    error: str = Field(min_length=1, max_length=2_000)


IntegrationPolicy = Literal["observe", "draft", "assisted", "trusted", "blocked"]


class IntegrationConfigure(BaseModel):
    display_name: str = Field(default="", max_length=120)
    policy: IntegrationPolicy = "observe"
    granted_scopes: list[str] = Field(default_factory=list, max_length=50)
    health: Literal["not_connected", "healthy", "needs_reauth", "error"] = "not_connected"


class IntegrationActionCreate(BaseModel):
    provider: Literal["gmail", "calendar", "telegram", "github", "drive"]
    action: str = Field(min_length=1, max_length=120)
    preview: str = Field(min_length=1, max_length=2_000)
    idempotency_key: str = Field(min_length=8, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict, max_length=30)


class IntegrationCredentialInput(BaseModel):
    """A provider token/secret submitted only over authenticated TLS."""
    secret: str = Field(min_length=1, max_length=20_000)
    kind: Literal["oauth_token", "bot_token", "api_token"]


class IntegrationActionDecision(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=2_000)
    edited_preview: str | None = Field(default=None, min_length=1, max_length=2_000)
    edited_payload: dict[str, Any] | None = Field(default=None, max_length=30)


class PushSubscriptionInput(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2_000)
    p256dh: str = Field(min_length=10, max_length=500)
    auth: str = Field(min_length=5, max_length=500)
