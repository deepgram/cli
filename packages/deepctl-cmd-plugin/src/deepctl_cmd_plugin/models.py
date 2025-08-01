"""Models for plugin management command."""

from enum import Enum

from deepctl_core.models import BaseModel
from pydantic import Field


class PluginAction(str, Enum):
    """Plugin management actions."""

    INSTALL = "install"
    LIST = "list"
    UPDATE = "update"
    REMOVE = "remove"
    SEARCH = "search"


class PluginPackage(BaseModel):
    """Information about a plugin package."""

    name: str
    version: str | None = None
    source: str | None = None  # pypi, git, local
    installed_version: str | None = None
    available_version: str | None = None
    entry_point: str | None = None
    is_builtin: bool = False


class PluginRegistryEntry(BaseModel):
    """Entry in the plugin registry."""

    name: str
    description: str
    version: str
    author: str | None = None
    url: str | None = None
    keywords: list[str] = Field(default_factory=list)
    # Package name for installation if different from name
    install_name: str | None = None


class PluginSearchResult(BaseModel):
    """Result from plugin search."""

    plugin: PluginRegistryEntry
    installed: bool = False
    installed_version: str | None = None


class PluginInstallOptions(BaseModel):
    """Options for plugin installation."""

    package: str
    version: str | None = None
    upgrade: bool = False
    pre: bool = False  # Allow pre-release versions
    force_reinstall: bool = False
    index_url: str | None = None
    extra_index_url: str | None = None
    git_url: str | None = None
    editable: bool = False


class PluginOperationResult(BaseModel):
    """Result of a plugin operation."""

    success: bool
    action: str  # One of PluginAction values
    package: str
    message: str
    error: str | None = None
    installed_version: str | None = None
    previous_version: str | None = None
