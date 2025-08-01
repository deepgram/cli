#!/usr/bin/env python3
"""Verify all packages are properly configured in build, version, and dependency systems."""

import os
import re
import sys
from pathlib import Path
from typing import Set, List, Dict


def get_all_packages() -> Dict[str, Set[str]]:
    """Get all packages from the packages directory."""
    packages_dir = Path("packages")
    packages = {
        "all": set(),
        "commands": set(),
        "core": set(),
        "plugins": set(),
    }

    for item in packages_dir.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            packages["all"].add(item.name)
            # Only treat as plugin if it's actually a plugin, not the plugin management command
            if "plugin" in item.name and item.name != "deepctl-cmd-plugin":
                packages["plugins"].add(item.name)
            elif "cmd" in item.name:
                packages["commands"].add(item.name)
            else:
                packages["core"].add(item.name)

    return packages


def get_packages_from_build_script() -> Set[str]:
    """Extract package list from build.py."""
    build_script = Path("scripts/build.py").read_text()

    # Find PACKAGES_TO_BUILD list
    match = re.search(
        r"PACKAGES_TO_BUILD = \[(.*?)\]", build_script, re.DOTALL
    )
    if not match:
        return set()

    packages = set()
    for line in match.group(1).split("\n"):
        if "packages/" in line:
            # Extract package name from path
            pkg_match = re.search(r'"packages/([\w-]+)"', line)
            if pkg_match:
                packages.add(pkg_match.group(1))

    return packages


def get_packages_from_version_script() -> Dict[str, Set[str]]:
    """Extract package lists from version.py."""
    version_script = Path("scripts/version.py").read_text()

    result = {"synchronized": set(), "dependency_updates": set()}

    # Find SYNCHRONIZED_PACKAGES list
    match = re.search(
        r"SYNCHRONIZED_PACKAGES = \[(.*?)\]", version_script, re.DOTALL
    )
    if match:
        for line in match.group(1).split("\n"):
            if "packages/" in line:
                pkg_match = re.search(r'"packages/([\w-]+)"', line)
                if pkg_match:
                    result["synchronized"].add(pkg_match.group(1))

    # Find packages in update_version function
    match = re.search(r"for pkg in \[(.*?)\]:", version_script, re.DOTALL)
    if match:
        for pkg in re.findall(r'"([\w-]+)"', match.group(1)):
            result["dependency_updates"].add(pkg)

    return result


def get_packages_from_dependencies() -> Set[str]:
    """Extract deepctl package dependencies from main pyproject.toml."""
    pyproject = Path("pyproject.toml").read_text()

    # Find dependencies section
    match = re.search(r"dependencies = \[(.*?)\]", pyproject, re.DOTALL)
    if not match:
        return set()

    packages = set()
    for line in match.group(1).split("\n"):
        # Look for deepctl-* packages
        pkg_match = re.search(r'"(deepctl-[\w-]+)', line)
        if pkg_match:
            packages.add(pkg_match.group(1))

    return packages


def check_built_packages() -> bool:
    """Check if packages have been built."""
    dist_dir = Path("dist")
    if not dist_dir.exists():
        return False

    wheels = list(dist_dir.glob("*.whl"))
    return len(wheels) > 0


def verify_consistency():
    """Verify all packages are consistently configured."""
    print("🔍 Verifying package configuration consistency...\n")

    # Get all data
    all_packages = get_all_packages()
    build_packages = get_packages_from_build_script()
    version_data = get_packages_from_version_script()
    dependencies = get_packages_from_dependencies()

    # Expected packages (all except plugins)
    expected_deps = all_packages["all"] - all_packages["plugins"]

    errors = []
    warnings = []

    # Check build script
    print("📦 Build Script (scripts/build.py):")
    missing_from_build = all_packages["all"] - build_packages
    extra_in_build = build_packages - all_packages["all"]

    if missing_from_build:
        errors.append(f"Missing from build: {missing_from_build}")
        print(f"  ❌ Missing: {missing_from_build}")
    if extra_in_build:
        warnings.append(f"Extra in build: {extra_in_build}")
        print(f"  ⚠️  Extra: {extra_in_build}")
    if not missing_from_build and not extra_in_build:
        print("  ✅ All packages included")

    # Check version script - synchronized list
    print("\n📝 Version Script - Synchronized List (scripts/version.py):")
    missing_from_version = all_packages["all"] - version_data["synchronized"]
    extra_in_version = version_data["synchronized"] - all_packages["all"]

    if missing_from_version:
        errors.append(f"Missing from version sync: {missing_from_version}")
        print(f"  ❌ Missing: {missing_from_version}")
    if extra_in_version:
        warnings.append(f"Extra in version sync: {extra_in_version}")
        print(f"  ⚠️  Extra: {extra_in_version}")
    if not missing_from_version and not extra_in_version:
        print("  ✅ All packages included")

    # Check version script - dependency updates
    print("\n📝 Version Script - Dependency Updates (scripts/version.py):")
    missing_from_updates = expected_deps - version_data["dependency_updates"]
    extra_in_updates = version_data["dependency_updates"] - expected_deps

    if missing_from_updates:
        errors.append(
            f"Missing from dependency updates: {missing_from_updates}"
        )
        print(f"  ❌ Missing: {missing_from_updates}")
    if extra_in_updates:
        warnings.append(f"Extra in dependency updates: {extra_in_updates}")
        print(f"  ⚠️  Extra: {extra_in_updates}")
    if not missing_from_updates and not extra_in_updates:
        print("  ✅ All non-plugin packages included")

    # Check main dependencies
    print("\n🔗 Main Package Dependencies (pyproject.toml):")
    missing_from_deps = expected_deps - dependencies
    extra_in_deps = dependencies - expected_deps

    if missing_from_deps:
        errors.append(f"Missing from dependencies: {missing_from_deps}")
        print(f"  ❌ Missing: {missing_from_deps}")
    if extra_in_deps:
        warnings.append(f"Extra in dependencies: {extra_in_deps}")
        print(f"  ⚠️  Extra: {extra_in_deps}")
    if not missing_from_deps and not extra_in_deps:
        print("  ✅ All non-plugin packages included")

    # Check plugin exclusion
    print("\n🔌 Plugin Handling:")
    plugins_in_deps = all_packages["plugins"] & dependencies
    if plugins_in_deps:
        errors.append(f"Plugins should not be dependencies: {plugins_in_deps}")
        print(f"  ❌ Plugins in dependencies: {plugins_in_deps}")
    else:
        print("  ✅ Plugins correctly excluded from dependencies")

    # Check if packages are built
    print("\n📦 Build Status:")
    if check_built_packages():
        dist_files = list(Path("dist").glob("*.whl"))
        print(f"  ✅ Found {len(dist_files)} built packages in dist/")
    else:
        warnings.append(
            "No built packages found in dist/ - run 'make build' first"
        )
        print("  ⚠️  No built packages found - run 'make build' first")

    # Summary
    print("\n" + "=" * 60)
    print("📊 Summary:")
    print(f"  Total packages: {len(all_packages['all'])}")
    print(f"  Command packages: {len(all_packages['commands'])}")
    print(f"  Core packages: {len(all_packages['core'])}")
    print(f"  Plugin packages: {len(all_packages['plugins'])}")

    if errors:
        print(f"\n❌ Found {len(errors)} error(s):")
        for error in errors:
            print(f"  - {error}")
        return False
    elif warnings:
        print(f"\n⚠️  Found {len(warnings)} warning(s):")
        for warning in warnings:
            print(f"  - {warning}")
        print("\n✅ All critical checks passed!")
        return True
    else:
        print("\n✅ All packages are properly configured!")
        return True


def main():
    """Main entry point."""
    os.chdir(Path(__file__).parent.parent)

    if verify_consistency():
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
