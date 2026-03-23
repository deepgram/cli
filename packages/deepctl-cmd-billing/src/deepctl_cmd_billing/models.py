"""Models for billing command."""

from __future__ import annotations

from typing import Any

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class BalanceInfo(BaseModel):
    balance_id: str = ""
    amount: float = 0.0
    units: str = ""


class BillingResult(BaseResult):
    balances: list[BalanceInfo] = Field(default_factory=list)
    breakdown: dict[str, Any] = Field(default_factory=dict)
