.PHONY: help build up down dev test test-unit test-integration test-e2e lint format type-check clean install migrate

# Default target
help: ## Show this help message
	@echo "agent-nexus — Multimodal Autonomous AI Agent"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------
install: ## Install dependencies
	pip install -e ".[dev]"
	playwright install chromium --with-deps || true

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------
build: ## Build Docker image
	docker compose build

up: ## Start all services (production mode)
	docker compose up -d

down: ## Stop all services
	docker compose down

dev: ## Start in development mode (hot reload)
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

logs: ## Tail logs from all services
	docker compose logs -f

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
migrate: ## Run database migrations (auto-create tables)
	python -c "import asyncio; from src.infra.db import init_db; asyncio.run(init_db())"

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
test: ## Run all tests
	pytest tests/ -v --tb=short

test-unit: ## Run unit tests only
	pytest tests/unit/ -v --tb=short -m unit

test-integration: ## Run integration tests (requires .env with real credentials)
	pytest tests/integration/ -v --tb=short -m integration

test-e2e: ## Run end-to-end tests
	pytest tests/e2e/ -v --tb=short -m e2e

test-cov: ## Run tests with coverage report
	pytest tests/ -v --tb=short --cov=src --cov-report=html --cov-report=term-missing

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------
lint: ## Lint code with ruff
	ruff check src/ tests/

format: ## Format code with ruff
	ruff format src/ tests/
	ruff check --fix src/ tests/

type-check: ## Run mypy type checking
	mypy src/

quality: lint type-check ## Run all code quality checks

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------
run: ## Run the FastAPI server locally (no Docker)
	uvicorn src.main:app --host 0.0.0.0 --port 7860 --reload

clean: ## Clean up generated files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf dist/ build/ *.egg-info
