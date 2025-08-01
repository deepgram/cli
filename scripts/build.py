#!/usr/bin/env python3
"""Build script for all deepctl distribution packages."""

import os
import sys
import shutil
import subprocess
from pathlib import Path

# Same package lists as version.py
PACKAGES_TO_BUILD = [
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


def clean_build_artifacts(package_dir: Path):
    """Clean build artifacts in a package directory."""
    for pattern in ["build", "dist", "*.egg-info"]:
        for path in package_dir.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def build_package(package_path: str, use_uv: bool = True):
    """Build a single package."""
    package_dir = Path(package_path)
    print(f"\n📦 Building {package_dir.name}...")

    # Clean previous builds
    clean_build_artifacts(package_dir)

    # Build
    if use_uv:
        subprocess.run(["uv", "build"], cwd=package_dir, check=True)
    else:
        subprocess.run(
            [sys.executable, "-m", "build"], cwd=package_dir, check=True
        )

    # Move artifacts to central dist directory (skip if building root)
    if package_path != ".":
        dist_dir = Path("dist")
        dist_dir.mkdir(exist_ok=True)

        package_dist = package_dir / "dist"
        if package_dist.exists():
            for file in package_dist.iterdir():
                if file.is_file():  # Only copy files, not directories
                    shutil.copy2(file, dist_dir / file.name)


def main():
    """Main build function."""
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)

    print("🔨 Building all deepctl distribution packages...")

    # Check if uv is available
    try:
        subprocess.run(["uv", "--version"], check=True, capture_output=True)
        use_uv = True
        print("✓ Using uv for builds (faster!)")
    except (subprocess.CalledProcessError, FileNotFoundError):
        use_uv = False
        print("✓ Using traditional build tools")

    # Clean central dist directory
    dist_dir = Path("dist")
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir()

    # Build all packages
    success_count = 0
    failed_packages = []

    for package in PACKAGES_TO_BUILD:
        try:
            build_package(package, use_uv)
            success_count += 1
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to build {package}: {e}")
            failed_packages.append(package)

    # Summary
    print("\n" + "=" * 60)
    if failed_packages:
        print(f"⚠️  Build completed with errors!")
        print(
            f"✅ Successfully built: {success_count}/{len(PACKAGES_TO_BUILD)} packages"
        )
        print(f"❌ Failed packages:")
        for pkg in failed_packages:
            print(f"   - {pkg}")
    else:
        print(f"✅ All {success_count} packages built successfully!")

    print(f"\n📁 Distribution files in {dist_dir}:")

    for file in sorted(dist_dir.iterdir()):
        size_mb = file.stat().st_size / (1024 * 1024)
        print(f"  {file.name:<50} ({size_mb:.2f} MB)")

    print(f"\n📊 Total: {len(list(dist_dir.iterdir()))} files")

    if not failed_packages:
        # Get absolute path for dist directory
        abs_dist_path = (Path.cwd() / "dist").absolute()

        print("\n🚀 To publish to PyPI:")
        print("  python scripts/publish.py")
        print("\n🧪 To test locally:")
        print("  # Option 1: Install with pipx (recommended for CLI tools)")
        print(
            f'  pipx install --python python3.12 --pip-args="--find-links {abs_dist_path}" dist/deepctl-0.1.0-py3-none-any.whl'
        )
        print("  # To reinstall/update: add --force flag")
        print(
            f'  pipx install --python python3.12 --pip-args="--find-links {abs_dist_path}" dist/deepctl-0.1.0-py3-none-any.whl --force'
        )
        print("\n  # Option 2: Install with pip in a virtual environment")
        print("  python3.12 -m venv test-env")
        print(
            "  source test-env/bin/activate  # On Windows: test-env\\Scripts\\activate"
        )
        print(
            f"  pip install --find-links {abs_dist_path} dist/deepctl-0.1.0-py3-none-any.whl"
        )
        print(
            "\n  Note: Python 3.13+ may not be supported by all dependencies yet"
        )


if __name__ == "__main__":
    main()
