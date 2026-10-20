from typing import Literal

from pydantic import BaseModel, Field


class DependencyStatus(BaseModel):
    status: Literal["ready", "unconfigured", "unavailable", "pending"]
    message: str


class Readiness(BaseModel):
    status: Literal["ready", "degraded"]
    stage: str = "P1-P3"
    dependencies: dict[str, DependencyStatus] = Field(default_factory=dict)
