"""Models for plugin management command."""

from enum import Enum

from deepctl_core.models import BaseModel


class PluginAction(str, Enum):
    """Plugin management actions."""

    INSTALL = "install"
    LIST = "list"
    UPDATE = "update"
    REMOVE = "remove"


class PluginPackage(BaseModel):
    """Information about a plugin package."""

    name: str
    version: str | None = None
    source: str | None = None  # pypi, git, local
    installed_version: str | None = None
    available_version: str | None = None
    entry_point: str | None = None
    is_builtin: bool = False


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
    action: PluginAction
    package: str
    message: str
    installed_version: str | None = None
    previous_version: str | None = None
    error: str | None = None
