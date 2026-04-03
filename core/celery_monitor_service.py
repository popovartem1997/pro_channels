"""
Сбор данных для страницы «Фоновые задачи Celery» (очереди, inspect, БД).
"""
from __future__ import annotations

from typing import Any

from django.conf import settings


def redis_queue_lengths(broker_url: str) -> tuple[dict[str, int | None], str | None]:
    if not (broker_url or '').strip():
        return {}, 'CELERY_BROKER_URL пуст'
    try:
        import redis

        r = redis.from_url(broker_url, socket_connect_timeout=3, socket_timeout=3)
        out: dict[str, int | None] = {}
        for q in ('import_history', 'prio', 'celery'):
            try:
                out[q] = int(r.llen(q))
            except Exception:
                out[q] = None
        return out, None
    except Exception as exc:
        return {}, str(exc)


def celery_inspect_bundle(timeout: float = 2.5) -> dict[str, Any]:
    from pro_channels.celery import app as celery_app

    out: dict[str, Any] = {
        'ping': None,
        'active': {},
        'reserved': {},
        'scheduled': {},
        'stats': {},
        'error': None,
    }
    try:
        insp = celery_app.control.inspect(timeout=timeout)
        if insp is None:
            out['error'] = 'inspect() вернул None (нет воркеров или брокер недоступен).'
            return out
        out['ping'] = insp.ping() or {}
        out['active'] = insp.active() or {}
        out['reserved'] = insp.reserved() or {}
        out['scheduled'] = insp.scheduled() or {}
        out['stats'] = insp.stats() or {}
    except Exception as exc:
        out['error'] = str(exc)
    return out


def task_category(task_name: str) -> str:
    n = (task_name or '').lower()
    if 'import_tg_history' in n:
        return 'import'
    if 'execute_parse_task' in n or 'check_parse_tasks' in n:
        return 'parse'
    if 'publish_post' in n or 'check_scheduled_posts' in n:
        return 'publish'
    return 'other'


def filter_tasks_by_category(
    worker_tasks: dict[str, list[dict]], category: str
) -> dict[str, list[dict]]:
    if category in ('', 'all'):
        return worker_tasks
    cat = category.lower().strip()
    filtered: dict[str, list[dict]] = {}
    for wname, tasks in (worker_tasks or {}).items():
        if not tasks:
            continue
        keep = [t for t in tasks if task_category(str(t.get('name') or '')) == cat]
        if keep:
            filtered[wname] = keep
    return filtered


def filter_tasks_by_name(
    worker_tasks: dict[str, list[dict]], name_sub: str
) -> dict[str, list[dict]]:
    sub = (name_sub or '').strip().lower()
    if not sub:
        return worker_tasks
    filtered: dict[str, list[dict]] = {}
    for wname, tasks in (worker_tasks or {}).items():
        if not tasks:
            continue
        keep = [t for t in tasks if sub in str(t.get('name') or '').lower()]
        if keep:
            filtered[wname] = keep
    return filtered


def beat_periodic_preview(limit: int = 40) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from django_celery_beat.models import PeriodicTask
    except Exception as exc:
        return [], str(exc)

    rows = []
    for pt in PeriodicTask.objects.filter(enabled=True).order_by('name')[:limit]:
        lr = getattr(pt, 'last_run_at', None)
        rows.append(
            {
                'name': pt.name,
                'task': pt.task,
                'enabled': pt.enabled,
                'last_run_at': lr.isoformat() if lr else None,
                'total_run_count': getattr(pt, 'total_run_count', None),
            }
        )
    return rows, None


def recent_task_results(
    *,
    limit: int = 40,
    task_name_contains: str = '',
    status: str = '',
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from django_celery_results.models import TaskResult
    except Exception as exc:
        return [], str(exc)

    qs = TaskResult.objects.all().order_by('-date_done', '-pk')[:800]
    name_sub = (task_name_contains or '').strip().lower()
    st = (status or '').strip().upper()
    out: list[dict[str, Any]] = []
    for tr in qs:
        tn = tr.task_name or ''
        if name_sub and name_sub not in tn.lower():
            continue
        if st and (tr.status or '').upper() != st:
            continue
        out.append(
            {
                'task_id': tr.task_id,
                'task_name': tn,
                'status': tr.status,
                'date_done': tr.date_done.isoformat() if tr.date_done else None,
                'category': task_category(tn),
            }
        )
        if len(out) >= limit:
            break
    return out, None


def settings_celery_summary() -> dict[str, Any]:
    return {
        'CELERY_BROKER_URL_tail': _redact_broker(getattr(settings, 'CELERY_BROKER_URL', '') or ''),
        'CELERY_TASK_ALWAYS_EAGER': getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False),
        'CELERY_TASK_DEFAULT_QUEUE': getattr(settings, 'CELERY_TASK_DEFAULT_QUEUE', 'celery'),
        'CELERY_TASK_ROUTES': getattr(settings, 'CELERY_TASK_ROUTES', None),
        'CELERY_WORKER_PREFETCH_MULTIPLIER': getattr(
            settings, 'CELERY_WORKER_PREFETCH_MULTIPLIER', 1
        ),
        'expected_worker_queues': 'import_history → prio → celery (см. docker-compose command)',
    }


def _redact_broker(url: str) -> str:
    if '@' in url:
        return url.split('@')[-1]
    return url[:120]


def support_log_commands() -> list[dict[str, str]]:
    """Команды для копирования в поддержку."""
    return [
        {
            'title': 'Воркер Celery (последние 300 строк)',
            'cmd': 'docker compose logs celery --tail=300',
        },
        {
            'title': 'Beat (расписание)',
            'cmd': 'docker compose logs celery-beat --tail=120',
        },
        {
            'title': 'Web',
            'cmd': 'docker compose logs web --tail=120',
        },
        {
            'title': 'Проверка брокера и воркеров из контейнера web',
            'cmd': 'docker compose exec web python manage.py celery_doctor',
        },
        {
            'title': 'Длины очередей Redis (по одной команде на очередь)',
            'cmd': (
                'docker compose exec redis sh -c '
                '"redis-cli LLEN import_history; redis-cli LLEN prio; redis-cli LLEN celery"'
            ),
        },
    ]


def parse_support_bundle_from_logs_hint() -> str:
    return (
        'Пришлите вывод команд выше **одним сообщением** (или файлами), '
        'плюс **время** (МСК), когда нажали «Опубликовать» / запустили импорт / парсинг, '
        'и **id поста** или **#запуска импорта** из интерфейса.'
    )
