# Simple Release Process

## The Correct Order ✅

### Automated Flow (`make release`):

1. **version.py** - Update all package versions
2. **auto-commit with [no-ci]** - Prevents duplicate CI runs
3. **build.py** - Build all packages
4. **verify_packages.py** - Verify everything
5. **tag.py** - Create git tag
6. **git push** - Push commits and tags
7. **GitHub Actions** runs **publish.py** (triggered by tag)

### Manual Flow:

1. **version.py** - Update all package versions
2. **manual commit** - Commit your changes (CI will run)
3. **build.py** - Build all packages
4. **verify_packages.py** - Verify everything
5. **tag.py** - Create git tag
6. **git push** - Push commits and tags
7. **GitHub Actions** runs **publish.py** (triggered by tag)

## Quick Commands

### Option 1: Automated Release (with [no-ci])

```bash
make release
# Enter version when prompted (e.g., 0.2.0)
# Then: git push origin main --tags
```

### Option 2: Manual Release (without [no-ci])

```bash
make release-manual
# Enter version when prompted (e.g., 0.2.0)
# Then: git push origin main --tags
```

### Option 3: Step by Step (Full Control)

```bash
# Step 1: Update versions
make version VERSION=0.2.0  # or just: make version

# Step 2: Commit (choose one)
make commit         # Normal commit
make commit NOCI=1  # Commit with [no-ci]

# Step 3: Build packages
make build

# Step 4: Verify everything
make verify-packages

# Step 5: Create tag
make tag

# Step 6: Push to GitHub
git push origin main --tags
```

### Option 4: Using Release Steps

```bash
# You can also use the numbered steps
make release-step-1 VERSION=0.2.0  # Version
make release-step-2 NOCI=1         # Commit with [no-ci]
make release-step-3                # Build
make release-step-4                # Verify
make release-step-5                # Tag
```

## Make Commands Explained

### Version Management

```bash
# Interactive version update
make version

# Specify version directly
make version VERSION=0.2.0
```

### Commit Management

```bash
# Normal commit (extracts version from pyproject.toml)
make commit
# Creates: "chore: bump version to v0.2.0"

# Commit with [no-ci] flag
make commit NOCI=1
# Creates: "chore: bump version to v0.2.0 [no-ci]"
```

### Complete Workflows

```bash
# Automated release (commits with [no-ci])
make release

# Manual release (commits without [no-ci])
make release-manual
```

## What Each Script Does

- **version.py** - Updates version in all pyproject.toml files and **init**.py
- **build.py** - Builds wheel and source distributions for all packages
- **verify_packages.py** - Checks that:
  - All packages are in build scripts ✓
  - All packages are in version scripts ✓
  - All non-plugin packages are dependencies ✓
  - Plugins are NOT in dependencies ✓
  - Packages have been built ✓
- **tag.py** - Creates an annotated git tag (e.g., v0.2.0)
- **publish.py** - Uploads to PyPI (run by GitHub Actions)

### About [no-ci]

The `make release` command automatically includes `[no-ci]` in the version bump commit to prevent duplicate CI runs. This is because:

- The automated flow commits and tags in one go
- The release is triggered by the tag, not the commit
- We don't need CI to run twice

**When running manually:** You typically DON'T need `[no-ci]` because you might want CI to verify your changes before creating the tag.

**Note:** GitHub Actions automatically skips workflows when it sees `[no-ci]`, `[ci skip]`, `[skip ci]`, `[no ci]`, `[skip actions]`, or `[actions skip]` in the commit message.

## Common Scenarios

### Normal Release

```bash
make release
# Enter: 0.2.0
git push origin main --tags
```

### Release After CI Verification

```bash
make release-manual  # or do steps manually
# Enter: 0.2.0
# Wait for CI to pass on the commit
git push origin main --tags
```

### Just Update Versions (No Release)

```bash
make version
# Enter: 0.2.0
git add -A
git commit -m "chore: bump version to v0.2.0"
```

### Verify Without Releasing

```bash
make verify-packages
```

### Build Without Releasing

```bash
make build
```

## If Something Goes Wrong

### Before Pushing

```bash
# Delete local tag
git tag -d v0.2.0

# Reset version changes
git reset --hard HEAD~1
```

### After Pushing

You can't delete published versions from PyPI, so be careful!
