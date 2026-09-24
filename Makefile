.PHONY: up down seed simulate backfill test types eval lint install workers api

UV ?= uv
COMPOSE ?= docker compose
export UV_PROJECT_ENVIRONMENT ?= $(CURDIR)/.venv311
export PYTHONPATH := packages/anpr_common/src:services/simulator/src:services/workers/src:services/api/src:services/ocr_engine/src:$(PYTHONPATH)

install:
	$(UV) sync --all-packages --python 3.11 --extra dev
	cd apps/dashboard && npx --yes pnpm@9 install

up:
	cp -n .env.example .env 2>/dev/null || true
	$(COMPOSE) up -d redpanda clickhouse postgres redis minio redpanda-init minio-init
	@echo "Waiting for healthchecks..."
	@$(COMPOSE) up -d --wait redpanda clickhouse postgres redis minio || true
	@sleep 3
	@echo "Infra is up."

down:
	$(COMPOSE) down -v

types:
	$(UV) run python scripts/generate_types.py

seed:
	$(UV) run python -m simulator.cli seed --synthetic

simulate:
	$(UV) run python -m simulator.cli simulate --speed $${SIM_SPEED:-60} --synthetic

backfill:
	$(UV) run python -m simulator.cli simulate --backfill-days $${BACKFILL_DAYS:-7} --synthetic

test:
	$(UV) run pytest packages/anpr_common/tests services/simulator/tests services/workers/tests services/api/tests services/ocr_engine/tests -q --tb=short
	@if [ "$${RUN_INTEGRATION}" = "1" ]; then $(UV) run pytest tests/integration -q --tb=short; fi

eval:
	mkdir -p reports
	$(UV) run python -m ocr_engine.eval --fixture services/ocr_engine/fixtures/eval_set

lint:
	$(UV) run ruff check packages services scripts tests

app-up:
	$(COMPOSE) --profile app up -d --build

ocr-up:
	$(COMPOSE) --profile ocr up -d --build

workers:
	$(UV) run python -m workers.cli run --worker all

api:
	$(UV) run anpr-api
