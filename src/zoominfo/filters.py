"""Intent Search filters verified against ZoomInfo's documentation and official CLI.

https://docs.zoominfo.com/reference/searchinterface_searchintent
https://github.com/Zoominfo/gtm-ai-cli (intent search)
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


LOOKUP_FIELDS = {"intent-topics", "countries", "states", "metro-regions", "industries",
                 "employee-count", "revenue-ranges", "tech-products", "management-levels"}
COMPANY_FILTERS = ("industry_codes", "employee_count", "revenue", "tech_products", "state", "metro_region")


class IntentFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    intent_topics: list[str] = Field(default_factory=list, max_length=50)
    min_signal_score: int | None = Field(default=None, ge=60, le=100)
    max_signal_score: int = Field(default=100, ge=60, le=100)
    signal_start_date: date | None = None
    signal_end_date: date | None = None
    audience_strength_min: Literal["A", "B", "C", "D", "E"] | None = None
    audience_strength_max: Literal["A", "B", "C", "D", "E"] | None = None
    state: str = Field(default="", max_length=300)
    metro_region: str = Field(default="", max_length=300)
    industry_codes: str = Field(default="", max_length=500)
    employee_count: str = Field(default="", max_length=300)
    revenue: str = Field(default="", max_length=300)
    tech_products: str = Field(default="", max_length=1000)

    @field_validator("intent_topics")
    @classmethod
    def clean_topics(cls, values):
        if any(not value.strip() or len(value.strip()) > 200 for value in values):
            raise ValueError("Intent topics must be nonempty names of at most 200 characters")
        return list(dict.fromkeys(value.strip() for value in values))

    @model_validator(mode="after")
    def ordered_ranges(self):
        if self.min_signal_score is not None and self.min_signal_score > self.max_signal_score:
            raise ValueError("Minimum signal score cannot exceed maximum signal score")
        if self.signal_start_date and self.signal_end_date and self.signal_start_date > self.signal_end_date:
            raise ValueError("Signal start date cannot be after the end date")
        # A is strongest and E weakest: E..A is the full range.
        if self.audience_strength_min and self.audience_strength_max and self.audience_strength_min < self.audience_strength_max:
            raise ValueError("Minimum audience strength cannot be stronger than maximum audience strength (A strongest, E weakest)")
        return self

    def zoominfo_attributes(self) -> dict:
        mapping = {"min_signal_score": "signalScoreMin", "max_signal_score": "signalScoreMax",
                   "signal_start_date": "signalStartDate", "signal_end_date": "signalEndDate",
                   "audience_strength_min": "audienceStrengthMin", "audience_strength_max": "audienceStrengthMax",
                   "state": "state", "metro_region": "metroRegion", "industry_codes": "industryCodes",
                   "employee_count": "employeeCount", "revenue": "revenue", "tech_products": "techAttributeTagList"}
        data = self.model_dump(mode="json")
        return {target: data[source] for source, target in mapping.items() if data[source] not in (None, "")}
