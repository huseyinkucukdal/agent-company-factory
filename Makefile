.PHONY: build test test-storage test-event-store test-clock test-identity test-cost test-approvals test-memory test-tools test-connector test-agent-runtime test-orchestrator test-security-agent test-factory test-board-api test-inter-company test-integration shell lint typecheck clean api ui ui-build ui-typecheck ui-lint ui-install up down logs acf

build:
	docker compose build

test:
	docker compose run --rm app python -m pytest

test-storage:
	docker compose run --rm app python -m pytest modules/storage/tests -xvs

test-event-store:
	docker compose run --rm app python -m pytest modules/event_store/tests -xvs

test-clock:
	docker compose run --rm app python -m pytest modules/clock/tests -xvs

test-identity:
	docker compose run --rm app python -m pytest modules/identity/tests -xvs

test-cost:
	docker compose run --rm app python -m pytest modules/cost/tests -xvs

test-approvals:
	docker compose run --rm app python -m pytest modules/approvals/tests -xvs

test-memory:
	docker compose run --rm app python -m pytest modules/memory/tests -xvs

test-tools:
	docker compose run --rm app python -m pytest modules/tools/tests -xvs

test-connector:
	docker compose run --rm app python -m pytest modules/connector/tests -xvs

test-agent-runtime:
	docker compose run --rm app python -m pytest modules/agent_runtime/tests -xvs

test-orchestrator:
	docker compose run --rm app python -m pytest modules/orchestrator/tests -xvs

test-security-agent:
	docker compose run --rm app python -m pytest modules/security_agent/tests -xvs

test-factory:
	docker compose run --rm app python -m pytest modules/factory/tests -xvs

test-board-api:
	docker compose run --rm app python -m pytest modules/board_api/tests -xvs

test-inter-company:
	docker compose run --rm app python -m pytest modules/inter_company/tests -xvs

test-integration:
	docker compose run --rm app python -m pytest modules/integration/tests -xvs

acf:
	docker compose run --rm app python -m modules.integration $(ARGS)

api:
	docker compose up api

ui:
	docker compose up ui

ui-build:
	docker compose run --rm ui npm run build

ui-typecheck:
	docker compose run --rm ui npm run typecheck

ui-lint:
	docker compose run --rm ui npm run lint

ui-install:
	docker volume rm ai-company_ui_node_modules 2>/dev/null || true
	docker compose build ui

up:
	docker compose up -d api ui

down:
	docker compose down

logs:
	docker compose logs -f api ui

shell:
	docker compose run --rm app bash

lint:
	docker compose run --rm app ruff check modules

typecheck:
	docker compose run --rm app mypy modules

clean:
	docker compose down -v
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis .coverage htmlcov
