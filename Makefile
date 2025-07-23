# ===================================================================
# deepctl Makefile
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
	@echo "Main Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -v '^\.' | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

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
	uv build

publish-test: build ## Publish to TestPyPI
	uv run twine upload --repository testpypi dist/*

publish: build ## Publish to PyPI (use with caution!)
	uv run twine upload dist/*

install-local: build ## Install package locally from built wheel
	uv tool install dist/*.whl --force

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