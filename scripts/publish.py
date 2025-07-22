#!/usr/bin/env python3
"""Publish all deepctl packages to PyPI."""

import subprocess
import sys
from pathlib import Path
from getpass import getpass


def check_twine():
    """Check if twine is installed."""
    try:
        subprocess.run(["twine", "--version"], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ twine is not installed!")
        print("Install it with: pip install twine")
        return False


def check_dist_files():
    """Check if distribution files exist."""
    dist_dir = Path("dist")

    if not dist_dir.exists():
        print("❌ No dist directory found. Run 'python scripts/build.py' first.")
        return None

    files = list(dist_dir.glob("*.whl")) + list(dist_dir.glob("*.tar.gz"))

    if not files:
        print("❌ No distribution files found in dist/")
        return None

    return files


def main():
    """Publish all packages to PyPI."""
    # Check prerequisites
    if not check_twine():
        sys.exit(1)

    files = check_dist_files()
    if not files:
        sys.exit(1)

    # Organize files by package
    packages = {}
    for file in files:
        # Extract package name from filename
        # e.g., deepctl_core-0.1.0-py3-none-any.whl -> deepctl-core
        parts = file.name.split("-")
        if len(parts) >= 2:
            package_name = parts[0].replace("_", "-")
            if package_name not in packages:
                packages[package_name] = []
            packages[package_name].append(file)

    print(f"📦 Found {len(packages)} packages to publish:")
    for pkg, pkg_files in sorted(packages.items()):
        print(f"  - {pkg}: {len(pkg_files)} files")

    # Check if we should use test PyPI
    if "--test" in sys.argv:
        repository = "testpypi"
        repo_url = "https://test.pypi.org/legacy/"
        print(f"\n🧪 Publishing to TestPyPI ({repo_url})")
    else:
        repository = "pypi"
        repo_url = "https://upload.pypi.org/legacy/"
        print(f"\n🚀 Publishing to PyPI ({repo_url})")

    # Confirm before proceeding
    print(f"\n⚠️  This will upload {len(files)} files to {repository}")
    response = input("Continue? [y/N] ")
    if response.lower() != 'y':
        print("Cancelled.")
        sys.exit(0)

    # Build twine command
    cmd = ["twine", "upload"]

    if "--test" in sys.argv:
        cmd.extend(["--repository", "testpypi"])

    # Add all files
    cmd.extend(str(f) for f in files)

    # Check for non-interactive mode
    if "--non-interactive" in sys.argv:
        print("\n📤 Uploading in non-interactive mode...")
        print("Make sure TWINE_USERNAME and TWINE_PASSWORD are set!")
    else:
        print("\n📤 Uploading packages...")
        print("You'll be prompted for your PyPI credentials.")

    # Execute upload
    try:
        subprocess.run(cmd, check=True)
        print("\n✅ Successfully published all packages!")

        if "--test" in sys.argv:
            print("\n📦 Install from TestPyPI with:")
            print("  pip install --index-url https://test.pypi.org/simple/ deepctl")
        else:
            print("\n📦 Install from PyPI with:")
            print("  pip install deepctl")

    except subprocess.CalledProcessError:
        print("\n❌ Publishing failed!")
        print("\nTroubleshooting:")
        print("1. Check your PyPI credentials")
        print("2. Ensure packages don't already exist with this version")
        print("3. For test uploads, use: python scripts/publish.py --test")
        sys.exit(1)


if __name__ == "__main__":
    main()
