# SPDX-License-Identifier: Apache-2.0
# SONiC ChaosLab — developer entry point. One target per workflow.
.DEFAULT_GOAL := help

PYTHON ?= python3.13
VENV := .venv
BIN := $(VENV)/bin
PY := $(BIN)/python
PIP := $(BIN)/pip

.PHONY: help setup build lint format test golden convert-lessons \
        lab-bootstrap lab-up lab-down lab-status lab-reset run api demo-answers clean clean-all all

help: ## Print all targets with their descriptions
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

$(PY):
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

setup: $(PY) ## Full setup from a fresh clone (venv, deps, .env, convert+validate lessons)
	$(PIP) install -e ".[dev]"
	@[ -f .env ] || cp .env.example .env
	$(PY) scripts/convert_lessons.py
	@echo "Setup complete. Try: make run"

build: ## Build wheel + sdist into dist/
	$(PY) -m build

lint: ## Run ruff check across the repo
	$(BIN)/ruff check .

format: ## Format the repo with ruff
	$(BIN)/ruff format .

test: ## Run pytest (passes with no network, no lab, no keys)
	$(BIN)/pytest

golden: ## Run the golden-set harness on the fake provider
	$(PY) scripts/run_golden.py

convert-lessons: ## Regenerate lessons/<id>/ YAML from lessons/<id>.md
	$(PY) scripts/convert_lessons.py

lab-bootstrap: ## One-time host setup: install containerlab + fetch the sonic-vs image
	bash scripts/setup_lab_host.sh

lab-up: ## Deploy the containerlab topology and poll until both leafs answer
	$(BIN)/chaoslab up

lab-down: ## Destroy the containerlab topology (graceful; idempotent)
	$(BIN)/chaoslab down

lab-status: ## Show lab deploy state and node readiness
	$(BIN)/chaoslab status

lab-reset: ## Restore the lab baseline (re-apply configs + startup ports)
	$(BIN)/chaoslab reset

run: ## Launch the interactive shell (defaults to lab_mode=mock, provider=fake)
	$(BIN)/chaoslab shell

api: ## Run the FastAPI dev server for the §5.2 API
	$(BIN)/uvicorn chaoslab.api.server:app --reload --port 8000

demo-answers: ## Generate demo_script.yaml via the real provider
	$(PY) scripts/gen_demo_answers.py

clean: ## Remove build artifacts and caches
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

clean-all: clean ## Also remove the virtualenv
	rm -rf $(VENV)

all: setup lint test build ## setup + lint + test + build
