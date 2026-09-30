# Changelog

- fix: OD `unique_vehicles` is counted with `uniqExact(plate_norm)` and cells below `OD_K_ANON` are suppressed.
- fix: alert dedupe uses Redis `SET NX EX` when `REDIS_URL` is set so replicas share one window.
