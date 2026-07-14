.PHONY: install lint format typecheck test run seed-synthetic

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

# Requires the API to already be running (e.g. `make run` in another shell) -- this target only
# generates and loads data through it, it never starts the server itself. See
# backend/src/energia/tools/synthetic/ and the root README's "Datos sintéticos" section.
BASE_URL ?= http://localhost:8000
SCALE ?= small
SEED ?= 42

seed-synthetic: ## Populate a running EnergIA instance with a deterministic synthetic dataset (BASE_URL/SCALE/SEED overridable)
	$(PY) -m energia.tools.synthetic --base-url $(BASE_URL) --scale $(SCALE) --seed $(SEED)
