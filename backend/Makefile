.PHONY: install lint format typecheck test run

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install: ## Create the virtualenv and install the project with dev dependencies (editable install)
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

lint: ## Static checks: ruff check (rules) + ruff format --check (style, no changes applied)
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

format: ## Auto-fix lint issues and apply ruff formatting in place
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

typecheck: ## mypy against src/ only (disallow_untyped_defs enforced there)
	$(VENV)/bin/mypy src

test: ## Full test suite (unit + integration) with coverage gate (fail_under=90, see pyproject.toml)
	$(VENV)/bin/pytest

run: ## Start the API with uvicorn --reload on port 8000
	$(VENV)/bin/uvicorn energia.main:app --reload --host 0.0.0.0 --port 8000
