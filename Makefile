.PHONY: help up down logs test test-unit test-integration test-accuracy lint format types check bench cov demo web-install web-dev web-build

BACKEND := backend
PY      := $(if $(wildcard $(BACKEND)/.venv/Scripts/python.exe),$(BACKEND)/.venv/Scripts/python.exe,$(if $(wildcard $(BACKEND)/.venv/bin/python),$(BACKEND)/.venv/bin/python,python))

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

up:              ## build and start Neo4j + API + frontend
	docker compose up -d --build

down:            ## stop everything and delete the database volume
	docker compose down -v

logs:            ## tail the API logs
	docker compose logs -f api

test:            ## unit + integration (integration skips itself if Neo4j is down)
	cd $(BACKEND) && $(abspath $(PY)) -m pytest tests/unit tests/integration -q

test-unit:       ## unit tests only, no database needed
	cd $(BACKEND) && $(abspath $(PY)) -m pytest tests/unit -q

test-integration: ## Cypher + HTTP tests against a live Neo4j
	docker compose up -d neo4j
	cd $(BACKEND) && $(abspath $(PY)) -m pytest tests/integration -q

test-accuracy:   ## the hand-labelled precision/recall gate (fetches pinned flask + requests)
	cd $(BACKEND) && $(abspath $(PY)) -m pytest tests/accuracy -q

cov:             ## unit tests with the 85% coverage gate
	cd $(BACKEND) && $(abspath $(PY)) -m pytest tests/unit -q --cov=faultline --cov-report=term-missing --cov-fail-under=85

lint:            ## ruff lint + format check
	cd $(BACKEND) && $(abspath $(PY)) -m ruff check . && $(abspath $(PY)) -m ruff format --check .

format:          ## apply ruff formatting and safe fixes
	cd $(BACKEND) && $(abspath $(PY)) -m ruff check . --fix && $(abspath $(PY)) -m ruff format .

types:           ## mypy --strict on the package
	cd $(BACKEND) && $(abspath $(PY)) -m mypy --strict faultline/

check: lint types cov ## everything CI's quick jobs run

bench:           ## benchmark five real repos (needs Neo4j for write + query numbers)
	cd $(BACKEND) && $(abspath $(PY)) benchmarks/run_benchmark.py

demo:            ## analyse this repository with no database at all
	cd $(BACKEND) && $(abspath $(PY)) -m faultline.cli analyze faultline --impact faultline.parsing.resolver.Resolver.resolve_call

web-install:     ## install frontend dependencies
	cd frontend && npm ci

web-dev:         ## start the Vite dev server (proxies /api to :8000)
	cd frontend && npm run dev

web-build:       ## type-check and build the frontend
	cd frontend && npm run build
