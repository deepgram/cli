.PHONY: help install install-dev test lint format typecheck clean build install-local run-help dev-setup all-checks

# Default target
help: ## Show this help message
	@echo "🔧 deepctl"
	@echo ""
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Development environment setup
dev-setup: ## Set up development environment
	uv venv
	uv pip install -e ".[dev]"
	@echo "✅ Development environment ready!"
	@echo "Activate with: source .venv/bin/activate (Linux/macOS) or .venv\\Scripts\\activate (Windows)"

install: ## Install runtime dependencies only
	uv pip install -e .

install-dev: ## Install development dependencies
	uv pip install -e ".[dev]"

install-test: ## Install test dependencies
	uv pip install -e ".[test]"

# Code quality and testing
test: ## Run tests with coverage
	uv run pytest

test-verbose: ## Run tests with verbose output
	uv run pytest -v

test-watch: ## Run tests in watch mode (requires pytest-watch)
	uv run ptw

lint: ## Run flake8 linter
	uv run flake8 src/ tests/

format: ## Format code with black
	uv run black src/ tests/

format-check: ## Check if code is formatted (CI-friendly)
	uv run black --check src/ tests/

typecheck: ## Run mypy type checker
	uv run mypy src/

# Combined checks
all-checks: format-check lint typecheck test ## Run all code quality checks

# Building and distribution
build: ## Build the package
	uv build

clean: ## Clean build artifacts and cache
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .mypy_cache/

# Installation and running
install-local: build ## Install package locally from built wheel
	uv tool install dist/*.whl --force

run:
	uv run python -m deepctl --help

run-help:
	uv run python -m deepctl transcribe --help

# Development workflow shortcuts
check: format lint typecheck ## Quick code quality check (no tests)

ci: all-checks ## Full CI pipeline (format, lint, typecheck, test)

# Pre-commit setup
pre-commit-install: ## Install pre-commit hooks
	uv run pre-commit install

pre-commit-run: ## Run pre-commit on all files
	uv run pre-commit run --all-files

# Documentation
docs-serve: ## Serve documentation locally (if using MkDocs or similar)
	@echo "📚 Documentation is in docs/ directory"
	@echo "📝 See README.md for usage instructions"

# Release helpers
version-patch: ## Bump patch version
	@echo "📦 Current version bump requires manual edit of pyproject.toml"
	@echo "🔧 Consider using bump2version or similar tool"

version-minor: ## Bump minor version
	@echo "📦 Current version bump requires manual edit of pyproject.toml"
	@echo "🔧 Consider using bump2version or similar tool"

# Quick development cycle
dev: format lint test ## Quick development cycle: format, lint, test

# Show project info
info: ## Show project information
	@echo "🔧 deepctl - Deepgram CLI"
	@echo "📁 $(shell pwd)"
	@echo "🐍 Python: $(shell python --version 2>/dev/null || echo 'Not found')"
	@echo "📦 uv: $(shell uv --version 2>/dev/null || echo 'Not found')"
	@echo "🎯 Virtual env: $(shell echo $$VIRTUAL_ENV || echo 'Not activated')" 