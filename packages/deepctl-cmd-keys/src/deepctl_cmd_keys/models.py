"""Models for keys command."""

from __future__ import annotations

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class KeyInfo(BaseModel):
    key_id: str = ""
    comment: str = ""
    scopes: list[str] = Field(default_factory=list)
    created: str = ""
    expiration_date: str = ""
    tags: list[str] = Field(default_factory=list)


class KeyCreatedInfo(KeyInfo):
    key: str = ""  # Only available on creation


class KeysResult(BaseResult):
    keys: list[KeyInfo] = Field(default_factory=list)
    count: int = 0
    created_key: KeyCreatedInfo | None = None
