#!/usr/bin/env python3
"""Create a git tag for the current version."""

import subprocess
import sys
from pathlib import Path


def get_current_version() -> str:
    """Get current version from root pyproject.toml."""
    import re
    pyproject = Path("pyproject.toml").read_text()
    match = re.search(r'^version = "(.+?)"', pyproject, re.MULTILINE)
    if match:
        return match.group(1)
    raise ValueError("Could not find version in pyproject.toml")


def tag_exists(tag_name: str) -> bool:
    """Check if a tag already exists."""
    try:
        subprocess.run(
            ["git", "rev-parse", tag_name],
            check=True,
            capture_output=True,
            text=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def create_tag(version: str, message: str = None) -> None:
    """Create an annotated git tag."""
    tag_name = f"v{version}"

    if tag_exists(tag_name):
        print(f"❌ Tag {tag_name} already exists!")
        sys.exit(1)

    if message is None:
        message = f"Release version {version}"

    try:
        # Create annotated tag
        subprocess.run(
            ["git", "tag", "-a", tag_name, "-m", message],
            check=True
        )
        print(f"✅ Created tag {tag_name}")
        print(f"\nNext steps:")
        print(f"  git push origin main")
        print(f"  git push origin {tag_name}")
        print(f"\nOr push both:")
        print(f"  git push origin main --tags")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to create tag: {e}")
        sys.exit(1)


def main():
    """Main entry point."""
    # Get current version
    try:
        version = get_current_version()
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)

    print(f"📦 Current version: {version}")

    # Optional: Allow custom message
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
    else:
        message = None

    # Create the tag
    create_tag(version, message)


if __name__ == "__main__":
    main()
