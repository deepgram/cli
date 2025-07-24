# Release Safety and Pre-Tag Verification

While Git doesn't have a native "pre-tag" hook, there are several effective approaches to prevent invalid releases from being tagged.

## Approach 1: Makefile Commands (Recommended) ✅

We have a complete release process in the Makefile:

```bash
# All-in-one safe release
make release
# Enter version when prompted (e.g., 0.2.0)
```

This command:

1. Updates all package versions
2. Commits with `[no-ci]` to avoid duplicate CI
3. Builds all packages
4. **Runs package verification** ✨
5. Creates the tag
6. Ready to push!

If any step fails, it stops immediately.

## Approach 2: Granular Control

Run each step individually:

```bash
make version          # Update versions
make build           # Build packages
make verify-packages # Verify configuration
make tag            # Create tag
```

## Approach 3: Git Alias

Add this to your `~/.gitconfig`:

```ini
[alias]
    safe-tag = "!f() { \
        if ! python3 scripts/verify_packages.py; then \
            echo 'Package verification failed!'; \
            exit 1; \
        fi; \
        git tag -a \"$1\" -m \"$2\"; \
    }; f"
```

Then use:

```bash
git safe-tag v0.2.0 "Release version 0.2.0"
```

## Approach 4: Pre-Push Hook

Create `.git/hooks/pre-push`:

```bash
#!/bin/bash
# Check if we're pushing tags
while read local_ref local_sha remote_ref remote_sha
do
    if [[ "$local_ref" =~ ^refs/tags/ ]]; then
        echo "Detected tag push, running verification..."
        if ! python3 scripts/verify_packages.py; then
            echo "❌ Package verification failed!"
            echo "Fix the issues before pushing tags."
            exit 1
        fi
    fi
done
exit 0
```

Make it executable:

```bash
chmod +x .git/hooks/pre-push
```

## Approach 5: CI/CD Protection

Your GitHub Actions workflow can also prevent bad releases:

```yaml
# In .github/workflows/release.yml
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Verify packages
        run: python scripts/verify_packages.py

  build:
    needs: verify # Only build if verification passes
    # ... rest of build job
```

## Best Practices

1. **Always use make release** for standard releases:

   ```bash
   make release
   ```

2. **Verify before manual releases**:

   ```bash
   make verify-packages
   ```

3. **Add verification to CI** as a safety net

4. **Regular verification** during development:
   ```bash
   python3 scripts/verify_packages.py
   ```

## What Gets Verified?

The package verification script checks:

- ✅ All packages are in build scripts
- ✅ All packages are in version scripts
- ✅ All non-plugin packages are dependencies
- ✅ Plugin packages are NOT dependencies
- ✅ Version consistency across packages
- ✅ Packages have been built

## Quick Commands

```bash
# Verify packages are configured correctly
make verify-packages

# Create a safe release
make release

# Just update versions (no tag)
make version

# Build without tagging
make build
```

## Additional Safety Checks

For extra safety, you can manually check:

```bash
# Ensure you're on main branch
git branch --show-current

# Check for uncommitted changes
git status

# Pull latest changes
git pull origin main
```

## Rollback

If something goes wrong after tagging locally (but before pushing):

```bash
# Delete local tag
git tag -d v0.2.0

# Reset version changes
git reset --hard HEAD~1

# Or just reset version files
git checkout -- '**/pyproject.toml' 'src/deepctl/__init__.py'
```
