# Build Error Fix - Missing Update Package

## Issue Description

The deepctl installation was failing with the error:

```
ERROR: Could not find a version that satisfies the requirement deepctl-cmd-update>=0.1.5 (from deepctl)
ERROR: No matching distribution found for deepctl-cmd-update>=0.1.5
```

## Root Cause

The `deepctl-cmd-update` package was listed as a dependency in the main `pyproject.toml` but was not being built during the build process.

## Problems Identified

1. **Missing from build script**: The package wasn't included in `PACKAGES_TO_BUILD` in `scripts/build.py`
2. **Missing from version script**: The package wasn't included in `SYNCHRONIZED_PACKAGES` in `scripts/version.py`
3. **Version mismatch**: The package had version `0.1.5` while all other packages were at `0.1.7`
4. **Inconsistent dependency version**: Main package required `>=0.1.5` while others used `>=0.1.7`

## Solution Applied

### 1. Updated `scripts/build.py`

Added `"packages/deepctl-cmd-update"` to the `PACKAGES_TO_BUILD` list.

### 2. Updated `scripts/version.py`

- Added `"packages/deepctl-cmd-update"` to the `SYNCHRONIZED_PACKAGES` list
- Added `"deepctl-cmd-update"` to the package list in the `update_version` function

### 3. Updated `pyproject.toml`

Changed the dependency version from `deepctl-cmd-update>=0.1.5` to `deepctl-cmd-update>=0.1.7` to match other packages.

### 4. Synchronized All Versions

Ran `python3 scripts/version.py 0.1.7` to ensure all packages had consistent versions.

### 5. Rebuilt All Packages

Ran `python3 scripts/build.py` to build all packages including the now-included update command.

## Verification

After applying the fixes, the installation was tested successfully:

```bash
python3 -m venv test-env
source test-env/bin/activate
pip install --find-links dist/ dist/deepctl-0.1.7-py3-none-any.whl
# Installation completed successfully
```

## Prevention

To prevent similar issues in the future:

1. When adding new command packages, ensure they are added to both `build.py` and `version.py`
2. Keep all package versions synchronized
3. Test the full installation process after adding new packages
4. Consider adding a CI check that verifies all packages listed in dependencies are actually built
