"""The settings models behind ``Config`` (pydantic).

Kept out of ``deepctl_core.config`` so that reading the config files, and
deciding what they may set, doesn't import pydantic: ``dg <cmd> --help``
runs that scan and never needs the models. ``Config`` imports this module
the first time it builds them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .hosts import DEFAULT_BASE_URL


class ProfileConfig(BaseModel):
    """Configuration for a specific profile."""

    api_key: str | None = None
    project_id: str | None = None
    base_url: str = DEFAULT_BASE_URL
    dg_token_expires_at: str | None = None


class OutputConfig(BaseModel):
    """Output formatting configuration."""

    format: str = Field(default="json", pattern="^(json|yaml|table|csv)$")
    color: bool = True
    quiet: bool = False
    verbose: bool = False


class PluginConfig(BaseModel):
    """Plugin configuration."""

    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)


class UpdateConfig(BaseModel):
    """Update configuration."""

    check_enabled: bool = True
    check_frequency: str = "daily"  # daily, weekly, never
    last_check: str | None = None
    installation_method: str | None = None
    installation_path: str | None = None
    cached_version_info: dict[str, Any] | None = None


class ToolsConfig(BaseModel):
    """External tools configuration."""

    ffprobe_path: str | None = None


class TelemetryConfig(BaseModel):
    """Telemetry setting, still accepted. This version sends no telemetry."""

    enabled: bool = True


class ApiConfig(BaseModel):
    """API host trust configuration. Read from the user config file only."""

    allowed_hosts: list[str] = Field(default_factory=list)


class DeepgramConfig(BaseModel):
    """Main configuration model."""

    default_profile: str = "default"
    active_profile: str | None = None  # Currently selected profile
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    output: OutputConfig = Field(default_factory=OutputConfig)
    plugins: PluginConfig = Field(default_factory=PluginConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
