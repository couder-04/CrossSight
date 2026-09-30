#!/usr/bin/env bash
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP:-redpanda:9092}"
echo "Waiting for Redpanda at ${BOOTSTRAP}..."
for i in $(seq 1 60); do
  if rpk cluster info -X brokers="${BOOTSTRAP}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

rpk topic create anpr.reads.v1 -p 12 -r 1 -X brokers="${BOOTSTRAP}" || true
# Widen partitions on existing clusters (no-op if already >= 12)
rpk topic add-partitions anpr.reads.v1 -n 12 -X brokers="${BOOTSTRAP}" || true
rpk topic create alerts.v1 -p 6 -r 1 -X brokers="${BOOTSTRAP}" || true
rpk topic add-partitions alerts.v1 -n 6 -X brokers="${BOOTSTRAP}" || true
rpk topic create analytics.flow.v1 -p 3 -r 1 -X brokers="${BOOTSTRAP}" || true
echo "Topics ready."
