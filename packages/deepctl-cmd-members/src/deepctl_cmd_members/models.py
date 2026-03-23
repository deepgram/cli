"""Models for members command."""

from __future__ import annotations

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class MemberInfo(BaseModel):
    member_id: str = ""
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    scopes: list[str] = Field(default_factory=list)


class InviteInfo(BaseModel):
    email: str = ""
    scope: str = ""


class MembersResult(BaseResult):
    members: list[MemberInfo] = Field(default_factory=list)
    invites: list[InviteInfo] = Field(default_factory=list)
    count: int = 0
