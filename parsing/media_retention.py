"""
Хранение локальных файлов медиа парсинга (parsed_items/…) с ограничением по возрасту.

Удаляются файлы старше срока из GlobalApiKeys / PARSE_MEDIA_RETENTION_DAYS,
очищается ParsedItem.media у старых записей; проход по диску (mtime);
подчищается старый staging imports/tg_to_max.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _url_to_parsed_items_rel(url: str) -> str | None:
    """/media/parsed_items/... → относительный путь под MEDIA_ROOT."""
    s = (url or '').strip()
    if not s:
        return None
    if s.startswith('http://') or s.startswith('https://'):
        path = urlparse(s).path or ''
    else:
        path = s
    marker = '/media/'
    if marker in path:
        idx = path.index(marker) + len(marker)
        rel = path[idx:].lstrip('/').replace('\\', '/')
    elif path.startswith('parsed_items/'):
        rel = path.lstrip('/')
    else:
        return None
    if not rel.startswith('parsed_items/'):
        return None
    return rel


def purge_parse_media_older_than(*, retention_days: int) -> dict[str, Any]:
    """
    Удалить медиа парсинга старше retention_days:
    1) ParsedItem с found_at до cutoff: удалить файлы из media[], обнулить JSON;
    2) обойти media/parsed_items и удалить файлы с mtime старше cutoff;
    3) старые файлы в media/imports/tg_to_max (хвосты после импорта).
    """
    from parsing.models import ParsedItem

    days = max(1, int(retention_days))
    cutoff = timezone.now() - timedelta(days=days)
    media_root = Path(getattr(settings, 'MEDIA_ROOT', settings.BASE_DIR / 'media'))

    stats: dict[str, Any] = {
        'retention_days': days,
        'cutoff': cutoff.isoformat(),
        'items_updated': 0,
        'files_from_db': 0,
        'files_sweep': 0,
    }

    qs = ParsedItem.objects.filter(found_at__lt=cutoff).only('pk', 'media')
    for item in qs.iterator(chunk_size=200):
        paths: list[str] = list(item.media or [])
        if not paths:
            continue
        for u in paths:
            rel = _url_to_parsed_items_rel(str(u))
            if not rel:
                continue
            abs_path = media_root / rel
            try:
                abs_r = abs_path.resolve()
                abs_r.relative_to(media_root.resolve())
            except (ValueError, OSError):
                continue
            if abs_r.is_file():
                try:
                    abs_r.unlink()
                    stats['files_from_db'] += 1
                except OSError as e:
                    logger.warning('parse media retention: unlink %s: %s', abs_r, e)
        item.media = []
        item.save(update_fields=['media'])
        stats['items_updated'] += 1

    base = media_root / 'parsed_items'
    if base.is_dir():
        for dirpath, _dirnames, filenames in os.walk(base, topdown=False):
            for name in filenames:
                fp = Path(dirpath) / name
                try:
                    if not fp.is_file():
                        continue
                    mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
                    if mtime < cutoff:
                        fp.unlink(missing_ok=True)
                        stats['files_sweep'] += 1
                except OSError as e:
                    logger.warning('parse media retention: sweep %s: %s', fp, e)
            for name in _dirnames:
                dp = Path(dirpath) / name
                try:
                    if dp.is_dir() and not any(dp.iterdir()):
                        dp.rmdir()
                except OSError:
                    pass

    # 3) Staging импорта TG→MAX: старые файлы (после успешного импорта подчищаются в воркере; тут — хвосты/сбои).
    imports_staging = media_root / 'imports' / 'tg_to_max'
    stats['imports_staging_files'] = 0
    if imports_staging.is_dir():
        for dirpath, _dirnames, filenames in os.walk(imports_staging):
            for name in filenames:
                fp = Path(dirpath) / name
                try:
                    if not fp.is_file():
                        continue
                    mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
                    if mtime < cutoff:
                        fp.unlink(missing_ok=True)
                        stats['imports_staging_files'] += 1
                except OSError as e:
                    logger.warning('parse media retention: imports staging %s: %s', fp, e)

    logger.info(
        'parse media retention: days=%s items_cleared=%s files_from_db=%s files_sweep=%s imports_staging=%s',
        days,
        stats['items_updated'],
        stats['files_from_db'],
        stats['files_sweep'],
        stats['imports_staging_files'],
    )
    return stats
