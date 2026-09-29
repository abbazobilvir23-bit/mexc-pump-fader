#!/usr/bin/env bash
# Пуш «снимка» проекта на GitHub для авто-деплоя на VPS.
# .github/workflows исключается (для него нужен отдельный workflow-scope токена).
# Сервер раз в 5 минут делает git fetch + reset --hard + docker-compose up -d --build.
set -euo pipefail

cd "$(dirname "$0")/.."

USER="${GH_USER:-abbazobilvir23-bit}"
REPO="${GH_REPO:-mexc-pump-fader}"
TOKEN="$(gh auth token)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git archive HEAD | tar -x -C "$TMP"
rm -rf "$TMP/.github"

(
  cd "$TMP"
  git init -q
  git add -A
  git -c user.email=deploy@local -c user.name=deploy commit -q -m "deploy $(date -u +%FT%TZ)"
)

git -C "$TMP" push -q -f "https://x-access-token:${TOKEN}@github.com/${USER}/${REPO}.git" HEAD:main
echo "pushed $(git -C "$TMP" rev-parse --short HEAD) -> ${USER}/${REPO}"