"""Models for init command."""

from __future__ import annotations

from typing import Any

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class TemplateInfo(BaseModel):
    """Summary info for a template (from list endpoint)."""

    name: str
    title: str
    description: str
    language: str
    framework: str | None = None
    category: str | None = None


class TemplateStats(BaseModel):
    """Repository statistics for a template."""

    stars: int = 0
    forks: int = 0
    last_updated: str | None = Field(default=None, alias="lastUpdated")

    model_config = {"populate_by_name": True}


class LifecycleStep(BaseModel):
    """A single lifecycle step from deepgram.toml."""

    command: str | list[str] | None = None
    message: str | None = None
    parallel: bool | None = None
    pre: dict[str, Any] | None = None
    post: dict[str, Any] | None = None


class TomlConfig(BaseModel):
    """Parsed deepgram.toml configuration."""

    check: LifecycleStep | None = None
    install: LifecycleStep | None = None
    start: LifecycleStep | None = None


class TemplateDetail(TemplateInfo):
    """Full template detail (from detail endpoint)."""

    sdk: str | None = None
    tags: list[str] = Field(default_factory=list)
    links: dict[str, str | None] = Field(default_factory=dict)
    stats: TemplateStats = Field(default_factory=TemplateStats)
    readme: str | None = None
    config: TomlConfig | None = None


class TemplateListResponse(BaseModel):
    """Response from the templates list API."""

    total: int = 0
    page: int = 1
    limit: int = 100
    total_pages: int = Field(default=1, alias="totalPages")
    items: list[TemplateInfo] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class InitResult(BaseResult):
    """Result from init command."""

    template: str | None = None
    directory: str | None = None
    installed: bool = False
    started: bool = False
