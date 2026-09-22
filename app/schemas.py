"""Validated request contracts. Unknown fields are rejected, including send on a search."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool, field_validator, model_validator

from src.zoominfo.filters import COMPANY_FILTERS, IntentFilters


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunCreate(IntentFilters):
    query: str = Field(min_length=3, max_length=1000, examples=["Companies exploring AI training in the UK"])
    mock: StrictBool = False
    limit: int = Field(default=10, ge=1, le=50)
    country: str | None = Field(default=None, max_length=100)
    min_tier: Literal["Cool", "Warm", "Hot"] = "Cool"
    contacts_per_lead: int = Field(default=1, ge=1, le=3)
    find_buyers: StrictBool = True
    enrich: StrictBool = True
    write_emails: StrictBool = True

    @model_validator(mode="after")
    def sample_filters(self):
        if self.mock and any(getattr(self, key) for key in COMPANY_FILTERS):
            raise ValueError("Company and state/metro filters require live ZoomInfo data")
        return self


class DraftEdit(RequestModel):
    expected_version: int = Field(ge=1)
    to_email: EmailStr
    to_name: str = Field(default="", max_length=200)
    subject: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=20000)

    @field_validator("subject", "to_name")
    @classmethod
    def no_header_newlines(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Email headers cannot contain newlines")
        return value


class Approval(RequestModel):
    expected_version: int = Field(ge=1)
    ready_to_send: StrictBool


class SendItem(RequestModel):
    email_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)


class SendReady(RequestModel):
    run_id: str = Field(min_length=1, max_length=64)
    emails: list[SendItem] = Field(min_length=1, max_length=50)

    @field_validator("emails")
    @classmethod
    def unique_ids(cls, value: list[SendItem]) -> list[SendItem]:
        if len({v.email_id for v in value}) != len(value):
            raise ValueError("Each email ID can appear only once")
        return value


class DeliveryResolution(RequestModel):
    expected_version: int = Field(ge=1)
    delivered: StrictBool


class KnowledgeEdit(RequestModel):
    profile: str = Field(min_length=50, max_length=60000)


class MicrosoftCallback(RequestModel):
    flow_handle: str = Field(min_length=43, max_length=43)
    response: dict[str, str]

    @field_validator("response")
    @classmethod
    def callback_values(cls, value):
        if set(value) - {"code", "state", "error"} or any(len(v) > 10000 for v in value.values()):
            raise ValueError("Invalid Microsoft callback")
        return value
