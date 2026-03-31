#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

echo "==> Pull latest code"
git pull

echo "==> Build & start containers"
docker compose up -d --build

echo "==> Run migrations"
docker compose exec -T web python manage.py migrate

echo "==> Collect static"
docker compose exec -T web python manage.py collectstatic --noinput

echo "==> Restart app containers"
docker compose restart web celery celery-beat

echo "Done."

