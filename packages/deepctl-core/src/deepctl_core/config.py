"""Cross-platform configuration management for deepctl."""

import os
from pathlib import Path
from typing import Any

import platformdirs
import yaml
from pydantic import BaseModel, Field


class ProfileConfig(BaseModel):
    """Configuration for a specific profile."""

    api_key: str | None = None
    project_id: str | None = None
    base_url: str = "https://api.deepgram.com"


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


class DeepgramConfig(BaseModel):
    """Main configuration model."""

    default_profile: str = "default"
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    output: OutputConfig = Field(default_factory=OutputConfig)
    plugins: PluginConfig = Field(default_factory=PluginConfig)


class Config:
    """Cross-platform configuration manager."""

    def __init__(
        self, config_path: str | None = None, profile: str | None = None
    ):
        """Initialize configuration manager.

        Args:
            config_path: Optional path to configuration file
            profile: Optional profile name to use
        """
        self.config_path = (
            Path(config_path)
            if config_path
            else self._get_default_config_path()
        )
        self.profile = profile
        self._config: DeepgramConfig
        self._load_config()

    def _get_default_config_path(self) -> Path:
        """Get the default configuration path for the current platform."""
        # Use platformdirs for cross-platform config directory
        config_dir = Path(platformdirs.user_config_dir("deepctl", "deepgram"))
        config_dir.mkdir(parents=True, exist_ok=True)

        # Migrate from old location if needed
        self._migrate_config_if_needed(config_dir)

        return config_dir / "config.yaml"

    def _migrate_config_if_needed(self, new_config_dir: Path) -> None:
        """Migrate config from old deepgram directory to new deepctl
        directory."""
        # Check for old config location
        old_config_dir = Path(
            platformdirs.user_config_dir("deepgram", "deepgram")
        )
        old_config_path = old_config_dir / "config.yaml"
        new_config_path = new_config_dir / "config.yaml"

        # If old config exists and new doesn't, migrate it
        if old_config_path.exists() and not new_config_path.exists():
            try:
                import shutil

                print(
                    f"Migrating config from {old_config_path} to "
                    f"{new_config_path}"
                )
                shutil.copy2(old_config_path, new_config_path)
                print("✓ Config migrated successfully")
            except Exception as e:
                print(f"Warning: Could not migrate config: {e}")

    def _get_project_config_path(self) -> Path:
        """Get the project-specific configuration path."""
        return Path.cwd() / "deepgram.yaml"

    def _load_config(self) -> None:
        """Load configuration from all sources."""
        # Start with default configuration
        self._config = DeepgramConfig()

        # Load from user config file
        if self.config_path.exists():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    user_config = yaml.safe_load(f)
                    if user_config:
                        self._merge_config(user_config)
            except Exception as e:
                # Don't fail on config load errors, just warn
                print(
                    f"Warning: Could not load config from "
                    f"{self.config_path}: {e}"
                )

        # Load from project config file
        project_config_path = self._get_project_config_path()
        if project_config_path.exists():
            try:
                with open(project_config_path, encoding="utf-8") as f:
                    project_config = yaml.safe_load(f)
                    if project_config:
                        self._merge_config(project_config)
            except Exception as e:
                print(
                    f"Warning: Could not load project config from "
                    f"{project_config_path}: {e}"
                )

        # Override with environment variables
        self._load_env_config()

    def _merge_config(self, config_dict: dict[str, Any]) -> None:
        """Merge configuration dictionary into current config."""
        # Deep merge configuration
        if "profiles" in config_dict:
            for profile_name, profile_config in config_dict[
                "profiles"
            ].items():
                if profile_name not in self._config.profiles:
                    self._config.profiles[profile_name] = ProfileConfig()

                # Update profile config
                for key, value in profile_config.items():
                    if hasattr(self._config.profiles[profile_name], key):
                        setattr(
                            self._config.profiles[profile_name], key, value
                        )

        # Update other top-level config
        for key, value in config_dict.items():
            if key != "profiles" and hasattr(self._config, key):
                if isinstance(getattr(self._config, key), BaseModel):
                    # Handle nested models
                    nested_model = getattr(self._config, key)
                    for nested_key, nested_value in value.items():
                        if hasattr(nested_model, nested_key):
                            setattr(nested_model, nested_key, nested_value)
                else:
                    setattr(self._config, key, value)

    def _load_env_config(self) -> None:
        """Load configuration from environment variables."""
        # Handle case-insensitive environment variables on Windows
        env_vars = {}
        for key, value in os.environ.items():
            env_vars[key.upper()] = value

        # Map environment variables to config
        env_mappings = {
            "DEEPGRAM_API_KEY": ("api_key", str),
            "DEEPGRAM_PROJECT_ID": ("project_id", str),
            "DEEPGRAM_BASE_URL": ("base_url", str),
            "DEEPGRAM_OUTPUT_FORMAT": ("output.format", str),
            "DEEPGRAM_OUTPUT_COLOR": ("output.color", bool),
            "DEEPGRAM_PROFILE": ("default_profile", str),
        }

        for env_key, (config_path, config_type) in env_mappings.items():
            if env_key in env_vars:
                value = env_vars[env_key]
                converted_value: Any = value
                if config_type == bool:
                    converted_value = value.lower() in (
                        "true",
                        "1",
                        "yes",
                        "on",
                    )

                self._set_config_value(config_path, converted_value)

    def _set_config_value(self, path: str, value: Any) -> None:
        """Set a configuration value using dot notation."""
        if "." in path:
            # Handle nested paths like "output.format"
            parts = path.split(".")
            current = self._config

            for part in parts[:-1]:
                current = getattr(current, part)

            setattr(current, parts[-1], value)
        else:
            # Handle top-level paths
            if path == "api_key" or path == "project_id" or path == "base_url":
                # These belong to the current profile
                profile_name = self.profile or self._config.default_profile
                if profile_name not in self._config.profiles:
                    self._config.profiles[profile_name] = ProfileConfig()
                setattr(self._config.profiles[profile_name], path, value)
            else:
                setattr(self._config, path, value)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value using dot notation."""
        try:
            if "." in key:
                parts = key.split(".")
                current = self._config

                for part in parts:
                    if hasattr(current, part):
                        current = getattr(current, part)
                    else:
                        return default

                return current
            else:
                return getattr(self._config, key, default)
        except (AttributeError, KeyError):
            return default

    def get_profile(self, profile_name: str | None = None) -> ProfileConfig:
        """Get configuration for a specific profile."""
        profile_name = (
            profile_name or self.profile or self._config.default_profile
        )

        if profile_name not in self._config.profiles:
            self._config.profiles[profile_name] = ProfileConfig()

        return self._config.profiles[profile_name]

    def save(self) -> None:
        """Save configuration to file."""

        config_dict = {
            "default_profile": self._config.default_profile,
            "profiles": {
                name: profile.model_dump(exclude_none=True)
                for name, profile in self._config.profiles.items()
            },
            "output": self._config.output.model_dump(exclude_none=True),
            "plugins": self._config.plugins.model_dump(exclude_none=True),
        }

        # Ensure config directory exists
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                config_dict, f, default_flow_style=False, sort_keys=False
            )

    def list_profiles(self) -> list[str]:
        """List all available profiles."""
        return list(self._config.profiles.keys())

    def create_profile(self, name: str, **kwargs: Any) -> None:
        """Create a new profile."""
        self._config.profiles[name] = ProfileConfig(**kwargs)
        self.save()

    def delete_profile(self, name: str) -> None:
        """Delete a profile."""
        if name in self._config.profiles:
            del self._config.profiles[name]
            self.save()

    @property
    def config_dir(self) -> Path:
        """Get the configuration directory."""
        return self.config_path.parent
