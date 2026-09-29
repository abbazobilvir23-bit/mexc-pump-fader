#!/usr/bin/env bash
# Периодический бэкап каталога data/ (статистика + архив) с ротацией.
set -uo pipefail

INTERVAL="${BACKUP_INTERVAL:-86400}"
KEEP="${BACKUP_KEEP:-7}"

while true; do
  ts="$(date -u +%Y%m%d-%H%M%S)"
  mkdir -p /app/backups
  tar czf "/app/backups/data-$ts.tar.gz" -C /app data 2>/dev/null \
    && echo "[backup] $(date -u +%FT%TZ) создан data-$ts.tar.gz" \
    || echo "[backup] ошибка архивации"

  # оставляем только последние KEEP архивов
  ls -1t /app/backups/data-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

  sleep "$INTERVAL"
done