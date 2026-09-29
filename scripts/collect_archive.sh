#!/usr/bin/env bash
# Автосбор архива истории MEXC. Запускать по таймеру/cron.
# MEXC хранит ~2 суток, поэтому копим данные регулярно и дописываем в архив.
set -euo pipefail

cd "$(dirname "$0")/.."

TOP="${TOP:-150}"
DAYS="${DAYS:-2}"
TIMEFRAME="${TIMEFRAME:-1m}"
OUT="${OUT:-data/archive.jsonl}"

exec .venv/bin/mpf collect \
  --top "$TOP" \
  --days "$DAYS" \
  --timeframe "$TIMEFRAME" \
  --append \
  --out "$OUT"