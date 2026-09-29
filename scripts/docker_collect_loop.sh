#!/usr/bin/env bash
# Цикл сбора истории внутри Docker-контейнера.
# Собирает 5m (для стратегии/бэктеста) и 1m (для архива) и спит 2 часа.
set -uo pipefail

INTERVAL="${COLLECT_INTERVAL:-7200}"
TOP="${TOP:-150}"
DAYS="${DAYS:-2}"

while true; do
  echo "[collect] $(date -u +%FT%TZ) 5m -> data/history_5m.jsonl"
  mpf collect --top "$TOP" --days "$DAYS" --timeframe 5m \
    --append --out data/history_5m.jsonl || echo "[collect] 5m ошибка, продолжаю"

  echo "[collect] $(date -u +%FT%TZ) 1m -> data/archive.jsonl"
  mpf collect --top "$TOP" --days "$DAYS" --timeframe 1m \
    --append --out data/archive.jsonl || echo "[collect] 1m ошибка, продолжаю"

  sleep "$INTERVAL"
done