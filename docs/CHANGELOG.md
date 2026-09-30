# Changelog

- fix: OD `unique_vehicles` is counted with `uniqExact(plate_norm)` and cells below `OD_K_ANON` are suppressed.
- fix: alert dedupe uses Redis `SET NX EX` when `REDIS_URL` is set so replicas share one window.
- fix: a widened trajectory search writes a second audit row and sets `data_status`.
- fix: dashboard middleware verifies the HS256 session cookie and blocks non-admins from `/admin`.
- fix: API startup marks stale processing exports and uploads as `worker_lost` and retry requeues them.
- fix: media uploads stream to disk in 1 MiB chunks and reject bodies over `MAX_UPLOAD_BYTES`.
- fix: production boots no longer overwrite an existing seed user's password.
- fix: dashboard session cookies are HttpOnly, SameSite=Lax, and Secure only in production.
- chore: apply `RAW_RETENTION_DAYS` to the ClickHouse reads TTL on API startup.
- chore: note that FileRegistry mismatch runs only when `REGISTRY_PATH` is set.
- chore: pin Python to `>=3.11,<3.13`.
- chore: cloned-plate alerts use a per-camera speed limit when both cameras have one.
- chore: reject plate bounding boxes that are not four coordinates.
- refactor: split transfer routes into imports, uploads, exports, and evidence.
- refactor: split analytics routes into heatmap, flow, and origin-destination modules.
- chore: run ruff, mypy, pytest, and the dashboard build in CI.
- chore: clear the first ruff and mypy failures so CI can pass.
- chore: keep heavy demo video and city-load run logs out of git.
