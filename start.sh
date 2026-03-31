#!/bin/bash
# ─────────────────────────────────────────────
# ProChannels — запуск всех сервисов
# Использование: ./start.sh
# ─────────────────────────────────────────────

cd "$(dirname "$0")"

echo "▶ Запуск ProChannels..."

# 1. Redis
if ! redis-cli ping &>/dev/null; then
  echo "  [1/3] Запускаю Redis..."
  redis-server --daemonize yes --logfile /tmp/redis-prochannels.log
  sleep 1
  if redis-cli ping &>/dev/null; then
    echo "  [1/3] Redis запущен ✓"
  else
    echo "  [1/3] Ошибка запуска Redis. Установите: brew install redis"
    exit 1
  fi
else
  echo "  [1/3] Redis уже работает ✓"
fi

# 2. Активируем virtualenv
source .venv/bin/activate

# 3. Celery worker
if pgrep -f "celery.*pro_channels.*worker" &>/dev/null; then
  echo "  [2/3] Celery уже работает ✓"
else
  echo "  [2/3] Запускаю Celery worker..."
  celery -A pro_channels worker --loglevel=warning \
    --logfile=/tmp/celery-prochannels.log \
    --pidfile=/tmp/celery-prochannels.pid \
    --detach
  sleep 2
  if pgrep -f "celery.*worker" &>/dev/null; then
    echo "  [2/3] Celery запущен ✓"
  else
    echo "  [2/3] Ошибка запуска Celery"
  fi
fi

# 4. Django
echo "  [3/3] Запускаю Django сервер..."
echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │  Сайт:    http://127.0.0.1:8000         │"
echo "  │  Админка: http://127.0.0.1:8000/admin/  │"
echo "  │  Логин:   admin / admin123              │"
echo "  │                                         │"
echo "  │  Остановить: Ctrl+C                     │"
echo "  └─────────────────────────────────────────┘"
echo ""

python manage.py runserver

# При остановке (Ctrl+C) — гасим Celery
echo ""
echo "  Останавливаю Celery..."
pkill -f "celery.*pro_channels" 2>/dev/null
echo "  Готово."
