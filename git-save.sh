#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -z "$(git status --porcelain)" ]]; then
  echo "Изменений нет."
  exit 0
fi
git add -A
echo "Введите сообщение коммита:"
read -r msg
if [[ -z "${msg// /}" ]]; then
  echo "Пустое сообщение — отмена."
  exit 1
fi
git commit -m "$msg"
git push
