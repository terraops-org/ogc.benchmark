# OGC Benchmark Makefile (uv: https://docs.astral.sh/uv/)

.PHONY: help install test test-live coverage lint syntax-check clean

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv with Python and all dependencies (uv sync)
	uv sync

test: ## Unit tests (no network)
	uv run pytest -m "not network"

test-live: ## Tests against live public services (needs network)
	uv run pytest -m network

coverage: ## Unit tests with a coverage report (terminal + htmlcov/)
	uv run pytest -m "not network" --cov=. --cov-report=term-missing --cov-report=html

lint: ## Format the code with black
	uv run black --line-length 120 *.py utils tests

syntax-check: ## Compile every module
	uv run python -m py_compile *.py utils/*.py

clean: ## Remove test and coverage output
	rm -rf htmlcov/ .coverage .pytest_cache/
	find . -name __pycache__ -not -path "./.venv/*" -prune -exec rm -rf {} +
