"""
Хранение локальных файлов медиа парсинга (parsed_items/…) с ограничением по возрасту и квоте на диск.

Удаляются файлы старше срока из GlobalApiKeys / PARSE_MEDIA_RETENTION_DAYS,
очищается ParsedItem.media у старых записей; проход по диску (mtime);
подчищается старый staging imports/tg_to_max.

Дополнительно (PARSE_MEDIA_DISK_QUOTA_BYTES > 0): суммарный размер parsed_items + imports/tg_to_max
не превышает квоты — лишнее удаляется с конца по самому старому mtime.
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


def _entry_urls_for_parsed_media(entry: Any) -> list[str]:
    """Элемент ParsedItem.media → список URL/путей (как в content.tasks._parsed_media_entries)."""
    if entry is None:
        return []
    if isinstance(entry, str):
        s = entry.strip()
        return [s] if s else []
    if isinstance(entry, dict):
        u = (entry.get('url') or entry.get('src') or '').strip()
        return [u] if u else []
    if isinstance(entry, (list, tuple)):
        out: list[str] = []
        for x in entry:
            out.extend(_entry_urls_for_parsed_media(x))
        return out
    return []


def strip_missing_parsed_item_local_files(*, media_root: Path | None = None) -> int:
    """
    Убрать из ParsedItem.media ссылки на локальные parsed_items/…, файлов которых уже нет.
    Возвращает число обновлённых записей.
    """
    from parsing.models import ParsedItem

    root = (media_root or Path(getattr(settings, 'MEDIA_ROOT', settings.BASE_DIR / 'media'))).resolve()
    updated = 0
    for item in ParsedItem.objects.exclude(media=[]).only('pk', 'media').iterator(chunk_size=200):
        media = list(item.media or [])
        new_media: list[Any] = []
        changed = False
        for entry in media:
            ok = True
            for u in _entry_urls_for_parsed_media(entry):
                rel = _url_to_parsed_items_rel(str(u))
                if not rel:
                    continue
                p = (root / rel).resolve()
                try:
                    p.relative_to(root)
                except ValueError:
                    continue
                if not p.is_file():
                    ok = False
                    break
            if ok:
                new_media.append(entry)
            else:
                changed = True
        if changed:
            item.media = new_media
            item.save(update_fields=['media'])
            updated += 1
    return updated


def enforce_parse_media_disk_quota(*, quota_bytes: int) -> dict[str, Any]:
    """
    Лимит на сумму размеров каталогов parsed_items и imports/tg_to_max под MEDIA_ROOT.
    Удаляет файлы в порядке возрастания mtime, пока сумма <= quota_bytes.
    """
    if quota_bytes <= 0:
        return {'skipped': True, 'reason': 'quota disabled', 'quota_bytes': quota_bytes}

    media_root = Path(getattr(settings, 'MEDIA_ROOT', settings.BASE_DIR / 'media')).resolve()
    roots = [
        media_root / 'parsed_items',
        media_root / 'imports' / 'tg_to_max',
    ]

    files: list[tuple[Path, int, float]] = []
    for base in roots:
        if not base.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                fp = Path(dirpath) / name
                try:
                    if not fp.is_file():
                        continue
                    st = fp.stat()
                    files.append((fp, int(st.st_size), float(st.st_mtime)))
                except OSError:
                    continue

    total = sum(sz for _fp, sz, _mt in files)
    stats: dict[str, Any] = {
        'quota_bytes': quota_bytes,
        'total_bytes_before': total,
        'deleted_files': 0,
        'freed_bytes': 0,
    }
    if total <= quota_bytes:
        stats['skipped'] = False
        return stats

    need_free = total - quota_bytes
    freed = 0
    deleted = 0
    files.sort(key=lambda t: t[2])
    for fp, size, _mt in files:
        if freed >= need_free:
            break
        try:
            fp.resolve().relative_to(media_root)
        except ValueError:
            continue
        try:
            fp.unlink(missing_ok=True)
            freed += size
            deleted += 1
        except OSError as e:
            logger.warning('parse media quota: unlink %s: %s', fp, e)

    stats['deleted_files'] = deleted
    stats['freed_bytes'] = freed
    stats['parsed_items_media_fixed'] = strip_missing_parsed_item_local_files(media_root=media_root)

    for base in roots:
        if not base.is_dir():
            continue
        for dirpath, dirnames, _filenames in os.walk(base, topdown=False):
            for name in dirnames:
                dp = Path(dirpath) / name
                try:
                    if dp.is_dir() and not any(dp.iterdir()):
                        dp.rmdir()
                except OSError:
                    pass

    logger.info(
        'parse media quota: quota=%s bytes before=%s deleted=%s freed=%s db_media_fixed=%s',
        quota_bytes,
        total,
        deleted,
        freed,
        stats['parsed_items_media_fixed'],
    )
    return stats


def run_parse_media_cleanup(*, retention_days: int) -> dict[str, Any]:
    """Возрастная очистка + опционально квота (Ключи API → PARSE_MEDIA_DISK_QUOTA_BYTES, иначе settings)."""
    from core.models import effective_parse_media_disk_quota_bytes

    stats = purge_parse_media_older_than(retention_days=retention_days)
    q = int(effective_parse_media_disk_quota_bytes())
    if q > 0:
        stats['quota'] = enforce_parse_media_disk_quota(quota_bytes=q)
    else:
        stats['quota'] = None
    return stats
