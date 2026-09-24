# ANPR Platform — Execution Plan

## Assumptions

1. Monorepo root is this workspace (`sih/`), not a nested `anpr-platform/` folder.
2. Default city: `CITY_QUERY="Pune, India"` with synthetic 20×20 grid fallback; Makefile defaults to `--synthetic`.
3. Map style: Carto Dark Matter (configurable via env).
4. Seeded users: `admin`/`admin123`, `operator`/`operator123`, `analyst`/`analyst123`.
5. Heavy OCR enhancer + Vahan registry are stubbed behind interfaces.
6. Model weights never committed; `make eval` uses MockRecognizer on synthetic fixtures.
7. `UV_PROJECT_ENVIRONMENT=.venv311` (Python 3.11); `npx pnpm@9` for the dashboard.
8. Host ports: Postgres **5433**, MinIO from **quay.io**; API may use **8002** if 8000 is busy.

## Phase checklist

### Phase 1: Foundations — DONE
- [x] docker-compose + healthchecks + init scripts
- [x] anpr_common (schemas, grammar, fuzzy, config, geo)
- [x] Makefile targets + unit tests (≥40 grammar cases)

### Phase 2: Simulator — DONE
- [x] OSM/synthetic graph, cameras, zones, scenarios.json
- [x] seed / simulate / backfill

### Phase 3: Workers — DONE
- [x] ingest, analytics, alerts (+ RegistryClient stub)

### Phase 4: API — DONE
- [x] REST + WS + RBAC (analyst 403 on trajectory)

### Phase 5: Dashboard — DONE
- [x] live / track / analytics / alerts / admin + httpOnly auth
- [x] production build

### Phase 6: OCR — DONE
- [x] pipeline, fusion, eval, training scripts, degraded mode

### Phase 7: Integration + docs — DONE
- [x] e2e tests (RUN_INTEGRATION=1)
- [x] README + PLAN
- [x] Definition of Done verified on this machine
