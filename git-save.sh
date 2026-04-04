#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -z "$(git status --porcelain)" ]]; then
  echo "Изменений нет."
  exit 0
fi
git add -A

branch=$(git rev-parse --abbrev-ref HEAD)
ts=$(date +'%Y-%m-%d %H:%M')
stat_line=$(git diff --cached --shortstat | sed 's/^[[:space:]]*//' || true)
preview=$(git diff --cached --name-only | head -4 | sed 's|.*/||' | tr '\n' ',' | sed 's/,$//' | sed 's/,/, /g')
total=$(git diff --cached --name-only | wc -l | tr -d ' ')
if [[ "${total:-0}" -gt 4 ]]; then
  if [[ -n "$preview" ]]; then
    preview="$preview, …"
  else
    preview="…"
  fi
fi

msg="Сохранение · $branch · $ts"
[[ -n "$preview" ]] && msg="$msg — $preview"
[[ -n "$stat_line" ]] && msg="$msg · $stat_line"

echo "Коммит: $msg"
git commit -m "$msg"
git push
