from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

TaskStatus = Literal["queued", "running", "waiting_approval", "completed", "failed", "cancelled"]


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20_000)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    requires_approval: bool = True


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
