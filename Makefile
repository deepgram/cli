# ===================================================================
# deepctl Makefile - Development Tools
# ===================================================================
# 
# Quick Start:
#   make dev-setup    # First time setup
#   make dev          # Daily development (format + lint + test)
#   make build        # Build packages
#   make help         # Show organized help
#
# For new contributors: see docs/Quick Start For Contributors.md
# ===================================================================

.PHONY: help
.DEFAULT_GOAL := help

# ===================================================================
# HELP & INFO
# ===================================================================

help: ## Show this help message
	@echo "🔧 deepctl - Deepgram CLI Development Tools"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "🚀 \033[1mQuick Start:\033[0m"
	@echo "  \033[36mdev-setup\033[0m            Set up development environment"
	@echo "  \033[36mdev\033[0m                  Format, lint, and test (full dev cycle)"
	@echo "  \033[36mbuild\033[0m                Build all packages for PyPI"
	@echo "  \033[36mtest\033[0m                 Run tests"
	@echo ""
	@echo "📦 \033[1mBuilding & Release:\033[0m"
	@echo "  \033[36mbuild\033[0m                Build all packages"
	@echo "  \033[36mrelease\033[0m              Full release process (version → build → tag)"
	@echo "  \033[36mpublish\033[0m              Publish to PyPI"
	@echo "  \033[36mverify-packages\033[0m      Verify package configuration"
	@echo ""
	@echo "🍺 \033[1mHomebrew:\033[0m"
	@echo "  \033[36mbuild-homebrew\033[0m       Build both Homebrew formulas (wheel + binary)"
	@echo "  \033[36mbuild-homebrew-wheels\033[0m Build wheel-based formula (recommended)"
	@echo "  \033[36mbuild-homebrew-binary\033[0m Build standalone binary formula"
	@echo ""
	@echo "🧪 \033[1mTesting:\033[0m"
	@echo "  \033[36mtest\033[0m                 Run tests (development)"
	@echo "  \033[36mtest-full\033[0m            Run tests on all Python versions"
	@echo "  \033[36mci\033[0m                   Run full CI pipeline"
	@echo ""
	@echo "🔧 \033[1mCode Quality:\033[0m"
	@echo "  \033[36mformat\033[0m               Auto-format code"
	@echo "  \033[36mlint\033[0m                 Run all linters"
	@echo "  \033[36mcheck\033[0m                Quick quality check (no tests)"
	@echo ""
	@echo "🧹 \033[1mUtilities:\033[0m"
	@echo "  \033[36mclean\033[0m                Clean build artifacts"
	@echo "  \033[36minfo\033[0m                 Show project information"
	@echo "  \033[36mhelp-all\033[0m             Show all available targets"
	@echo ""
	@echo "For more targets, run: \033[36mmake help-all\033[0m"

help-all: ## Show all available targets
	@echo "🔧 deepctl - All Available Targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -v '^\.' | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2}'

info: ## Show project information
	@echo "🔧 deepctl - Deepgram CLI"
	@echo "📁 $(shell pwd)"
	@echo "🐍 Python: $(shell python --version 2>/dev/null || echo 'Not found')"
	@echo "📦 uv: $(shell uv --version 2>/dev/null || echo 'Not found')"
	@echo "🎯 Virtual env: $(shell echo $$VIRTUAL_ENV || echo 'Not activated')"

# ===================================================================
# DEVELOPMENT SETUP
# ===================================================================

dev-setup: ## Set up complete development environment
	uv venv
	uv pip install -e ".[dev]"
	@echo "✅ Development environment ready!"
	@echo "Activate with: source .venv/bin/activate (Linux/macOS) or .venv\\Scripts\\activate (Windows)"

install: ## Install runtime dependencies only
	uv pip install -e .

install-dev: ## Install all development dependencies (includes testing)
	uv pip install -e ".[dev]"

# ===================================================================
# QUICK DEVELOPMENT WORKFLOWS
# ===================================================================

dev: format lint-fix test ## Run full development cycle: format, fix lints, test
	@echo "✅ Development cycle complete!"

check: format-check lint typecheck ## Quick quality check (no tests)
	@echo "✅ Quick check complete!"

ci: ## Run full CI pipeline (all Python versions + lint)
	uv run tox -p auto

# ===================================================================
# TESTING
# ===================================================================

test: ## Run tests with pytest (development mode)
	uv run pytest --all

test-quick: ## Run tests quickly (no coverage)
	uv run pytest -x

test-verbose: ## Run tests with verbose output
	uv run pytest -xvs --all

test-watch: ## Run tests in watch mode (requires pytest-watch)
	uv run ptw

test-full: ## Run tests on all Python versions using tox
	uv run tox

test-py310: ## Test with Python 3.10
	uv run tox -e py310

test-py311: ## Test with Python 3.11
	uv run tox -e py311

test-py312: ## Test with Python 3.12
	uv run tox -e py312

test-parallel: ## Run all tox environments in parallel
	uv run tox -p auto

# ===================================================================
# CODE QUALITY
# ===================================================================

## Formatting
format: ## Auto-format code with black
	uv run black src/ packages/**/src

format-check: ## Check code formatting (no changes)
	uv run black --check src/ packages/**/src

## Linting
lint: ## Run all linters via tox
	uv run tox -e lint

lint-fix: ## Run ruff with auto-fix
	uv run ruff check --fix src/ packages/**/src

lint-check: ## Run ruff without fixes
	uv run ruff check src/ packages/**/src

## Type Checking
typecheck: ## Run mypy type checker
	uv run mypy src/ packages/**/src

## All Checks
quality: format-check lint-check typecheck ## Run all quality checks

# ===================================================================
# BUILD & DISTRIBUTION  
# ===================================================================

build: clean ## Build the package
	uv run scripts/build.py

publish-test: build ## Publish to TestPyPI
	uv run scripts/publish.py --test

publish: build ## Publish to PyPI (use with caution!)
	uv run scripts/publish.py

# ===================================================================
# RELEASE MANAGEMENT
# ===================================================================

verify-packages: ## Verify all packages are properly configured
	@echo "🔍 Verifying package configuration..."
	@python3 scripts/verify_packages.py

# Version management with optional VERSION parameter
version: ## Update version in all packages (usage: make version VERSION=0.2.0)
ifdef VERSION
	@python3 scripts/version.py $(VERSION)
else
	@read -p "Enter new version: " VERSION && \
	python3 scripts/version.py $$VERSION
endif

# Commit with optional [no-ci] flag
commit: ## Commit changes (usage: make commit or make commit NOCI=1)
ifdef NOCI
	@echo "💾 Committing with [no-ci]..."
	@git add -A && \
	git commit -m "chore: bump version to v$$(python3 -c "import re; content=open('pyproject.toml').read(); print(re.search(r'version = \"(.+?)\"', content).group(1))") [no-ci]"
else
	@echo "💾 Committing..."
	@git add -A && \
	git commit -m "chore: bump version to v$$(python3 -c "import re; content=open('pyproject.toml').read(); print(re.search(r'version = \"(.+?)\"', content).group(1))")"
endif

tag: ## Create git tag for current version
	@python3 scripts/tag.py

# Modular release steps that use the base targets
release-step-1: ## Step 1: Update versions (usage: make release-step-1 VERSION=0.2.0)
	@echo "📝 Step 1: Updating versions..."
	@$(MAKE) version VERSION=$(VERSION)
	@echo "✅ Step 1 complete: Versions updated to $(VERSION)"

release-step-2: ## Step 2: Commit changes (usage: make release-step-2 NOCI=1)
	@echo "💾 Step 2: Committing changes..."
	@$(MAKE) commit NOCI=$(NOCI)
	@echo "✅ Step 2 complete: Changes committed"

release-step-3: ## Step 3: Build packages
	@echo "🔨 Step 3: Building packages..."
	@$(MAKE) build
	@echo "✅ Step 3 complete: Packages built"

release-step-4: ## Step 4: Verify configuration
	@echo "🔍 Step 4: Verifying configuration..."
	@$(MAKE) verify-packages
	@echo "✅ Step 4 complete: Configuration verified"

release-step-5: ## Step 5: Create tag
	@echo "🏷️  Step 5: Creating tag..."
	@$(MAKE) tag
	@echo "✅ Step 5 complete: Tag created"

# Full automated release (with [no-ci])
release: ## Run complete release process (version -> commit -> build -> verify -> tag)
	@echo "🚀 Starting automated release process..."
	@read -p "Enter version (e.g., 0.2.0): " VERSION && \
	$(MAKE) release-step-1 VERSION=$$VERSION && \
	$(MAKE) release-step-2 NOCI=1 && \
	$(MAKE) release-step-3 && \
	$(MAKE) release-step-4 && \
	$(MAKE) release-step-5 && \
	echo "✅ Release complete! Now run: git push origin main --tags"

# Manual release process (without [no-ci])
release-manual: ## Run release process with manual commit (no [no-ci])
	@echo "🚀 Starting manual release process..."
	@read -p "Enter version (e.g., 0.2.0): " VERSION && \
	$(MAKE) release-step-1 VERSION=$$VERSION && \
	$(MAKE) release-step-2 && \
	$(MAKE) release-step-3 && \
	$(MAKE) release-step-4 && \
	$(MAKE) release-step-5 && \
	echo "✅ Release complete! Now run: git push origin main --tags"

# ===================================================================
# RUNNING THE CLI
# ===================================================================

run: ## Run the CLI (show help)
	uv run python -m deepctl --help

run-version: ## Show CLI version
	uv run python -m deepctl --version

# ===================================================================
# CLEANUP
# ===================================================================

clean: ## Clean all build artifacts and caches
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf packages/**/*.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf .tox/

clean-env: ## Remove virtual environment
	rm -rf .venv/

# ===================================================================
# PRE-COMMIT HOOKS
# ===================================================================

pre-commit-install: ## Install pre-commit hooks
	uv run pre-commit install

pre-commit-run: ## Run pre-commit on all files
	uv run pre-commit run --all-files

# ===================================================================
# DOCUMENTATION
# ===================================================================

docs-list: ## List documentation files
	@echo "📚 Documentation files:"
	@ls -la docs/

docs-serve: ## Serve documentation (placeholder)
	@echo "📚 Documentation is in docs/ directory"
	@echo "📝 See README.md for usage instructions"

# ===================================================================
# HOMEBREW TESTING
# ===================================================================

.PHONY: build-binary build-homebrew-wheels build-homebrew-binary build-homebrew
.PHONY: tap-wheels tap-binary tap tap-install tap-uninstall

# Main Homebrew targets (user-facing)
build-homebrew: build-homebrew-wheels build-homebrew-binary ## Build both Homebrew formulas (wheel + binary)

build-homebrew-wheels: build ## Build wheel-based Homebrew formula (recommended)
	@./homebrew/scripts/generate-wheel-tap.sh

build-homebrew-binary: build-binary ## Build standalone binary Homebrew formula
	@./homebrew/scripts/generate-tap.sh

# Advanced/internal Homebrew targets
build-binary: ## Build standalone binary for Homebrew
	@./homebrew/scripts/build-binary.sh

tap-wheels: build-homebrew-wheels ## Generate wheel formula only (no building)
	@echo "✅ Wheel-based formula ready: homebrew/dist/deepctl.rb"

tap-binary: build-homebrew-binary ## Generate binary formula only (no building)
	@echo "✅ Binary-based formula ready: homebrew/dist/deepctl-standalone.rb"

tap: tap-binary ## Generate binary formula (legacy, use build-homebrew-binary instead)

tap-install: ## Test: Install deepctl via local Homebrew formula
	@./homebrew/scripts/tap-install.sh

tap-uninstall: ## Test: Uninstall deepctl from Homebrew
	@./homebrew/scripts/tap-uninstall.sh

# ===================================================================
# ALIASES (for convenience)
# ===================================================================

.PHONY: t tc tl tf q f l

t: test              ## Alias for test
tc: test-full        ## Alias for test-full (tox complete)
tl: lint             ## Alias for lint
tf: test-parallel    ## Alias for test-parallel (tox fast)
q: check             ## Alias for check (quick)
f: format            ## Alias for format
l: lint-fix          ## Alias for lint-fix 