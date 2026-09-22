"""Response contracts shown in Swagger and exported OpenAPI."""
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import RunCreate
from src.models import Contact, IntentSignal


class LeadView(BaseModel):
    id: str
    run_id: str
    company_id: str
    company_name: str
    website: str = ""
    score: int = 0
    tier: str = ""
    score_reasons: list[str] = Field(default_factory=list)
    signals: list[IntentSignal] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)


class EmailView(BaseModel):
    id: str
    run_id: str
    lead_id: str | None = None
    company_name: str
    to_name: str
    to_email: str
    subject: str
    body: str
    programme: str
    status: Literal["draft", "ready", "sending", "sent", "failed", "unknown"]
    ready_to_send: bool
    version: int
    approved_version: int | None = None
    approved_at: str | None = None
    approved_by: str | None = None
    sent_by: str | None = None
    sender_email: str | None = None
    sent_at: str | None = None
    last_error: str | None = None
    message_id: str | None = None
    updated_at: str


class RunProgress(BaseModel):
    plan: dict | None = None
    progress: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RunSummary(BaseModel):
    id: str
    query: str
    status: Literal["queued", "running", "completed", "failed"]
    mock: bool
    request: RunCreate
    result: RunProgress
    error: str | None = None
    created_at: str
    updated_at: str
    lead_count: int
    email_count: int
    ready_count: int
    sent_count: int


class RunDetail(RunSummary):
    leads: list[LeadView]
    emails: list[EmailView]


class RunList(BaseModel):
    items: list[RunSummary]
    total: int


class LeadList(BaseModel):
    items: list[LeadView]
    total: int


class EmailList(BaseModel):
    items: list[EmailView]
    total: int


class DeliveryResult(BaseModel):
    email_id: str
    status: Literal["sent", "skipped", "failed", "unknown"]
    detail: str
    message_id: str | None = None


class SendResponse(BaseModel):
    results: list[DeliveryResult]
    sent_count: int
