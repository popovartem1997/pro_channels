"""
Краткий снимок состояния хоста для дашборда суперпользователя.
Все вызовы обёрнуты: сбой отдельной метрики не валит страницу.
"""
from __future__ import annotations

import os
import socket
import sys
import time
from typing import Any

from django import get_version as django_get_version


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _bytes_to_gb(n: float | int) -> float:
    return round(float(n) / (1024**3), 2)


def _fmt_uptime(seconds: float | int) -> str:
    s = int(seconds)
    d, r = divmod(s, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts: list[str] = []
    if d:
        parts.append(f'{d} д')
    if h:
        parts.append(f'{h} ч')
    parts.append(f'{m} мин')
    return ' '.join(parts) if parts else '0 мин'


def _disk_root_path() -> str:
    if os.name == 'nt':
        return os.environ.get('SystemDrive', 'C:') + '\\'
    return '/'


def get_server_stats_for_dashboard() -> dict[str, Any]:
    """
    Возвращает словарь для шаблона: ok, error?, memory, cpu, load, disk, swap, meta.
    """
    import psutil

    out: dict[str, Any] = {
        'ok': True,
        'error': None,
        'memory': {
            'total_gb': None,
            'used_gb': None,
            'available_gb': None,
            'percent': None,
            'error': None,
        },
        'swap': None,
        'cpu': {
            'percent': None,
            'logical': None,
            'physical': None,
            'error': None,
        },
        'load': None,
        'disk': {
            'path': None,
            'total_gb': None,
            'used_gb': None,
            'free_gb': None,
            'percent': None,
            'error': None,
        },
        'meta': {
            'hostname': _safe(socket.gethostname, '—'),
            'python_version': sys.version.split()[0],
            'django_version': django_get_version(),
            'uptime_human': None,
            'boot_at': None,
        },
    }

    try:
        vm = psutil.virtual_memory()
        out['memory'].update(
            {
                'total_gb': _bytes_to_gb(vm.total),
                'used_gb': _bytes_to_gb(vm.used),
                'available_gb': _bytes_to_gb(vm.available),
                'percent': round(vm.percent, 1),
            }
        )
    except Exception as e:
        out['memory']['error'] = str(e)

    try:
        sm = psutil.swap_memory()
        if sm.total:
            out['swap'] = {
                'total_gb': _bytes_to_gb(sm.total),
                'used_gb': _bytes_to_gb(sm.used),
                'percent': round(sm.percent, 1),
            }
    except Exception:
        out['swap'] = None

    try:
        # первый замер может быть неточным; для дашборда достаточно
        pct = psutil.cpu_percent(interval=0.15)
        out['cpu'].update(
            {
                'percent': round(pct, 1),
                'logical': psutil.cpu_count(logical=True),
                'physical': psutil.cpu_count(logical=False),
            }
        )
    except Exception as e:
        out['cpu']['error'] = str(e)

    out['load'] = _safe(lambda: os.getloadavg())  # type: ignore[attr-defined]

    try:
        path = _disk_root_path()
        du = psutil.disk_usage(path)
        out['disk'].update(
            {
                'path': path,
                'total_gb': _bytes_to_gb(du.total),
                'used_gb': _bytes_to_gb(du.used),
                'free_gb': _bytes_to_gb(du.free),
                'percent': round(du.percent, 1),
            }
        )
    except Exception as e:
        out['disk']['error'] = str(e)

    try:
        boot = psutil.boot_time()
        out['meta']['boot_at'] = boot
        out['meta']['uptime_human'] = _fmt_uptime(time.time() - boot)
    except Exception:
        pass

    return out
