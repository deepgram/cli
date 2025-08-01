#!/usr/bin/env python3
"""Version management for deepctl packages."""

import re
import sys
from pathlib import Path
from typing import List

# Define package versioning groups
SYNCHRONIZED_PACKAGES = [
    ".",  # Root package
    "packages/deepctl-core",
    "packages/deepctl-shared-utils",
    "packages/deepctl-cmd-login",
    "packages/deepctl-cmd-projects",
    "packages/deepctl-cmd-transcribe",
    "packages/deepctl-cmd-usage",
    "packages/deepctl-cmd-debug",
    "packages/deepctl-cmd-debug-audio",
    "packages/deepctl-cmd-debug-browser",
    "packages/deepctl-cmd-debug-network",
    "packages/deepctl-cmd-mcp",
    "packages/deepctl-cmd-update",
    "packages/deepctl-cmd-plugin",
    "packages/deepctl-plugin-example",  # Example plugin (not a dependency)
]

# Packages that version independently (community plugins)
INDEPENDENT_PACKAGES = [
    # Community plugins would go here
]


def get_current_version() -> str:
    """Get current version from root pyproject.toml."""
    pyproject = Path("pyproject.toml").read_text()
    match = re.search(r'^version = "(.+?)"', pyproject, re.MULTILINE)
    if match:
        return match.group(1)
    raise ValueError("Could not find version in pyproject.toml")


def update_version(package_path: str, new_version: str):
    """Update version in a package's pyproject.toml."""
    path = Path(package_path) / "pyproject.toml"
    content = path.read_text()

    # Update version line
    content = re.sub(
        r'^version = ".+?"',
        f'version = "{new_version}"',
        content,
        flags=re.MULTILINE,
    )

    # Update internal dependencies to use >= current version
    # Handle various dependency formats
    for pkg in [
        "deepctl-core",
        "deepctl-shared-utils",
        "deepctl-cmd-debug",
        "deepctl-cmd-login",
        "deepctl-cmd-projects",
        "deepctl-cmd-transcribe",
        "deepctl-cmd-usage",
        "deepctl-cmd-debug-audio",
        "deepctl-cmd-debug-browser",
        "deepctl-cmd-debug-network",
        "deepctl-cmd-mcp",
        "deepctl-cmd-update",
        "deepctl-cmd-plugin",
    ]:
        # Update simple dependency format: "package-name"
        content = re.sub(
            rf'"{pkg}"(?=,|\s*\])', f'"{pkg}>={new_version}"', content
        )
        # Update existing version constraints: "package-name>=X.X.X"
        content = re.sub(
            rf'"{pkg}>=[\d.]+"', f'"{pkg}>={new_version}"', content
        )

    path.write_text(content)
    print(f"Updated {path} to version {new_version}")


def update_init_version(new_version: str):
    """Update version in src/deepctl/__init__.py."""
    init_file = Path("src/deepctl/__init__.py")
    if init_file.exists():
        content = init_file.read_text()
        content = re.sub(
            r'^__version__ = ".+?"',
            f'__version__ = "{new_version}"',
            content,
            flags=re.MULTILINE,
        )
        init_file.write_text(content)
        print(f"Updated {init_file} to version {new_version}")


def validate_version(version: str) -> bool:
    """Validate semantic version format."""
    # Basic semver: MAJOR.MINOR.PATCH
    if re.match(r"^\d+\.\d+\.\d+$", version):
        return True
    # With pre-release: MAJOR.MINOR.PATCH-PRERELEASE
    if re.match(r"^\d+\.\d+\.\d+-\w+(\.\w+)*$", version):
        return True
    # With build metadata: MAJOR.MINOR.PATCH+BUILD
    if re.match(r"^\d+\.\d+\.\d+\+\w+(\.\w+)*$", version):
        return True
    return False


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/version.py <new_version>")
        print("Example: python scripts/version.py 0.2.0")
        print("         python scripts/version.py 1.0.0-rc.1")
        print("         python scripts/version.py 1.0.0+build.123")
        sys.exit(1)

    new_version = sys.argv[1]

    # Validate version format
    if not validate_version(new_version):
        print(f"Invalid version format: {new_version}")
        print("Use semantic versioning: MAJOR.MINOR.PATCH")
        print(
            "Optional: MAJOR.MINOR.PATCH-PRERELEASE or MAJOR.MINOR.PATCH+BUILD"
        )
        sys.exit(1)

    current = get_current_version()
    print(f"Updating all packages from {current} to {new_version}")

    # Update all synchronized packages
    for package in SYNCHRONIZED_PACKAGES:
        update_version(package, new_version)

    # Update __init__.py
    update_init_version(new_version)

    print(
        f"\n✅ Successfully updated {len(SYNCHRONIZED_PACKAGES)} packages to v{new_version}"
    )
    print("\n📋 Next steps:")
    print("1. Review changes: git diff")
    print(f"2. Commit: git commit -am 'chore: bump version to v{new_version}'")
    print(f"3. Tag: git tag v{new_version}")
    print("4. Push: git push && git push --tags")


if __name__ == "__main__":
    main()
