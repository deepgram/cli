# ===================================================================
# deepctl Makefile - Development Tools
# ===================================================================
#
# Quick Start:
#   make dev-setup    # First time setup
#   make dev          # Daily development (format + lint + test)
#   make help         # Show organized help
#
# For new contributors: see README.md
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
	@echo "  \033[36mtest\033[0m                 Run tests"
	@echo ""
	@echo "🧪 \033[1mTesting:\033[0m"
	@echo "  \033[36mtest\033[0m                 Run tests (development)"
	@echo "  \033[36mcheck\033[0m                Quick quality check (no tests)"
	@echo ""
	@echo "🔧 \033[1mCode Quality:\033[0m"
	@echo "  \033[36mformat\033[0m               Auto-format code"
	@echo "  \033[36mlint\033[0m                 Run all linters"
	@echo "  \033[36mtypecheck\033[0m            Run mypy type checker"
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

check: format-check lint-check typecheck ## Quick quality check (no tests)
	@echo "✅ Quick check complete!"

# ===================================================================
# TESTING
# ===================================================================

test: ## Run tests with pytest
	uv run pytest

test-quick: ## Run tests quickly (no coverage)
	uv run pytest -x

test-verbose: ## Run tests with verbose output
	uv run pytest -xvs

test-watch: ## Run tests in watch mode (requires pytest-watch)
	uv run ptw

# ===================================================================
# CODE QUALITY
# ===================================================================

## Formatting
format: ## Auto-format code with ruff
	uv run ruff format src/ packages/**/src

format-check: ## Check code formatting (no changes)
	uv run ruff format --check src/ packages/**/src

## Linting
lint: format-check lint-check typecheck ## Run all linters
	@echo "✅ All linters passed!"

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
# RELEASE MANAGEMENT
# ===================================================================

build: clean ## Build all packages into dist/
	@pip install build
	@for pkg in . packages/*; do \
		if [ -f "$$pkg/pyproject.toml" ]; then \
			echo "  Building $$pkg..."; \
			python -m build "$$pkg" --outdir dist/; \
		fi; \
	done

verify-packages: ## Verify built packages with twine
	@pip install twine
	twine check dist/*

readmes: ## Generate sub-package READMEs from pyproject.toml metadata
	python3 scripts/generate_readmes.py

readmes-check: ## Check sub-package READMEs are up to date
	python3 scripts/generate_readmes.py --check

floors-check: ## Check intra-workspace dependency floors (root must pin workspace versions)
	python3 scripts/check_dependency_floors.py

floors-fix: ## Pin root dependency floors to the current workspace versions
	python3 scripts/check_dependency_floors.py --fix

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
# ALIASES (for convenience)
# ===================================================================

.PHONY: t tl q f l

t: test              ## Alias for test
tl: lint             ## Alias for lint
q: check             ## Alias for check (quick)
f: format            ## Alias for format
l: lint-fix          ## Alias for lint-fix
