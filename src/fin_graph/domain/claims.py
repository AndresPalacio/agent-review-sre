"""How well cited evidence backs a claim: shared by every verification step."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ClaimStatus(StrEnum):
    """How well the cited evidence backs a claim."""

    SUPPORTED = "supported"
    WEAK = "weak"
    UNSUPPORTED = "unsupported"


class ClaimCheck(BaseModel):
    """The verdict on one claim and why."""

    statement: str
    status: ClaimStatus
    reason: str
