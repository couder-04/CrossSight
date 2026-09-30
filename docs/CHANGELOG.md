# Changelog

- fix: OD `unique_vehicles` is counted with `uniqExact(plate_norm)` and cells below `OD_K_ANON` are suppressed.
- fix: alert dedupe uses Redis `SET NX EX` when `REDIS_URL` is set so replicas share one window.
- fix: a widened trajectory search writes a second audit row and sets `data_status`.
- fix: dashboard middleware verifies the HS256 session cookie and blocks non-admins from `/admin`.
- fix: API startup marks stale processing exports and uploads as `worker_lost` and retry requeues them.
