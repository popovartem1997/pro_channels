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
echo "OK: $msg"
