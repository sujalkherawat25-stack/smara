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

    @model_validator(mode="after")
    def validate_executor_contract(self):
        if self.executor_kind in {"desktop", "sandbox"} and not self.required_capability:
            raise ValueError("desktop and sandbox steps must declare a required_capability")
        return self


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20_000)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    requires_approval: bool = True
    steps: list[TaskStepInput] = Field(default_factory=lambda: [TaskStepInput(name="agent.execute")], min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_step_graph(self):
        if not self.requires_approval and any(
            step.executor_kind in {"desktop", "sandbox"} for step in self.steps
        ):
            raise ValueError("desktop and sandbox work must require approval")
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
    # Hosted tasks are authorised in Smara Web.  Desktop tasks are authorised
    # by the paired device only; the hosted service merely plans and audits.
    approval_mode: Literal["hosted", "desktop"] = "hosted"
    result: str | None = Field(default=None, max_length=20_000)
    created_at: datetime
    updated_at: datetime


class ScheduleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20_000)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    interval_seconds: int = Field(ge=60, le=2_592_000)
    starts_at: datetime | None = None
    requires_approval: bool = True
    steps: list[TaskStepInput] = Field(default_factory=lambda: [TaskStepInput(name="agent.execute")], min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_step_graph(self):
        if not self.requires_approval and any(
            step.executor_kind in {"desktop", "sandbox"} for step in self.steps
        ):
            raise ValueError("desktop and sandbox work must require approval")
        for index, step in enumerate(self.steps):
            if len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError("a step cannot depend on the same step twice")
            if any(parent < 0 or parent >= index for parent in step.depends_on):
                raise ValueError("steps may only depend on earlier steps")
        return self


class ScheduleView(BaseModel):
    id: str
    account_id: str
    workspace_id: str
    title: str
    objective: str
    interval_seconds: int
    next_run_at: datetime
    enabled: bool
    requires_approval: bool
    last_run_at: datetime | None = None
    last_task_id: str | None = None
    created_at: datetime
    updated_at: datetime


class SkillCreate(BaseModel):
    """A strict manifest is validated again by the skill protocol at runtime."""

    manifest: dict[str, Any] = Field(min_length=1, max_length=64)


class SkillTestRequest(BaseModel):
    passed: bool
    run_id: str = Field(min_length=1, max_length=200)


class SkillTeachRequest(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    version: str = Field(min_length=5, max_length=32)
    description: str = Field(min_length=1, max_length=2_000)
    workflow: list[dict[str, Any]] = Field(min_length=2, max_length=6)
    tests: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    rollback: dict[str, Any] | None = Field(default=None, max_length=8)


class ChatRequest(BaseModel):
    """A short conversational turn; durable work belongs in TaskCreate."""
    message: str = Field(min_length=1, max_length=20_000)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=160)
    model_profile: str | None = Field(default=None, min_length=1, max_length=64)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)


class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    memory_used: bool
    model: str | None = None
    tools_used: int = 0


class ToolInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=20)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)


class CliPairingStart(BaseModel):
    name: str = Field(default="Smara CLI", min_length=1, max_length=120)


class CliPairingExchange(BaseModel):
    code: str = Field(min_length=12, max_length=160)


class CliDeviceAuthorize(BaseModel):
    """Browser approval for a CLI device authorization request."""
    device_code: str = Field(min_length=20, max_length=220)


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
    published_at: str | None = None
    content_sha256: str | None = None
    excerpt: str | None = None
    claim: str | None = None
    confidence: float | None = None
    citation_label: str | None = None
    error: str | None = None
    domain_policy: Literal["allowed", "unclassified", "blocked"] = "unclassified"
    quality_flags: list[str] = Field(default_factory=list)
    agreement_count: int = 0
    verification_notes: str | None = None


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


class ExecutorClaim(BaseModel):
    """Polling policy for a paired desktop executor.

    ``auto_approve_safe`` is deliberately narrow: it may release only
    read-only local work. Writes, terminal commands, and any unknown
    capability still require explicit Desktop approval.

    ``auto_approve_local`` is the explicit Desktop "Approve for me" mode. It
    can release declared local capabilities, but only for desktop-only steps
    and only when the paired executor advertises each required capability.
    """
    auto_approve_safe: bool = False
    auto_approve_local: bool = False


class ExecutorComplete(BaseModel):
    result: str = Field(min_length=1, max_length=20_000)


class ExecutorFailure(BaseModel):
    error: str = Field(min_length=1, max_length=2_000)


class ExecutorProgress(BaseModel):
    """A bounded, non-sensitive executor status update for the task ledger."""
    message: str = Field(min_length=1, max_length=500)


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
