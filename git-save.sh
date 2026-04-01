#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -z "$(git status --porcelain)" ]]; then
  echo "Изменений нет."
  exit 0
fi
files="$(git status --porcelain | awk '{print $2}' | sort -u | tr '\n' ' ' | sed 's/ *$//')"
ts="$(date '+%Y-%m-%d %H:%M')"
msg="chore: git save (${ts})"
if [[ -n "${files}" ]]; then
  msg="${msg} — ${files}"
fi
git add -A
git commit -m "$msg"
git push

# ---- Sync .env to server (not stored in git) ----
# Defaults: adjust if needed
SERVER_USER="${SERVER_USER:-artem}"
SERVER_HOST="${SERVER_HOST:-72.56.241.203}"
SERVER_PATH="${SERVER_PATH:-/home/artem/pro_channels/pro_channels}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"

if [[ -f ".env" ]]; then
  if [[ -f "$SSH_KEY" ]]; then
    scp -i "$SSH_KEY" ".env" "${SERVER_USER}@${SERVER_HOST}:${SERVER_PATH}/.env"
  else
    scp ".env" "${SERVER_USER}@${SERVER_HOST}:${SERVER_PATH}/.env"
  fi
  echo "OK: .env synced to ${SERVER_USER}@${SERVER_HOST}:${SERVER_PATH}/.env"
else
  echo "WARN: .env not found рядом со скриптом, пропускаю sync"
fi

echo "OK: $msg"
