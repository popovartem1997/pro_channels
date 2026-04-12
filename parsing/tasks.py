"""
Celery задачи для парсинга контента и AI рерайта.

Парсеры:
- Telegram — через Telethon (user API)
- VK — через vk_api (wall.get)
- RSS — через feedparser
- Яндекс Дзен — через requests + BeautifulSoup

Периодическая задача check_parse_tasks() проверяет активные задачи
и запускает их по расписанию (cron формат из ParseTask.schedule_cron).
"""
import asyncio
import hashlib
import logging
import re
import secrets
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


def _telethon_asyncio_run(coro):
    """
    Запуск корутины Telethon в отдельном цикле (аналог asyncio.run).

    В воркерах Celery и внутри asyncio.to_thread нельзя полагаться на «текущий» loop потока,
    если ранее использовали new_event_loop() без set_event_loop — см. stats.tasks._get_tg_post_stats.
    Дополнительно защищаемся от случайного вложенного asyncio.run в том же потоке.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        'Telethon: в этом потоке уже есть running event loop; '
        'не вызывайте _telethon_asyncio_run из async-кода.'
    )


def _telethon_client_kwargs():
    """Единые параметры клиента Telethon: меньше фоновых задач и предсказуемые ретраи (как в импорте TG→MAX)."""
    from django.conf import settings

    from core.telethon_proxy import merge_telethon_proxy_from_settings

    connect_timeout = int(getattr(settings, 'TG_HISTORY_IMPORT_TELETHON_CONNECT_TIMEOUT', 90) or 90)
    base = dict(
        connection_retries=5,
        request_retries=5,
        timeout=int(connect_timeout),
        receive_updates=False,
    )
    return merge_telethon_proxy_from_settings(base)


def _telethon_sess_hash(owner_id: int) -> str:
    """
    32 hex — общий суффикс для Redis-ключа и fcntl-файла (тот же смысл, что и раньше у lock key).
    """
    from django.conf import settings

    base = Path(settings.BASE_DIR) / 'media' / 'telethon_sessions'
    try:
        base = base.resolve()
    except Exception:
        pass
    owner_session = base / f'user_{int(owner_id)}.session'
    default_session = base / 'user_default.session'
    try:
        if owner_session.is_file():
            token = str(owner_session.resolve())
        else:
            token = str(default_session.resolve())
    except Exception:
        token = f'{base}/user_{int(owner_id)}.session|fallback_default'
    return hashlib.sha256(token.encode('utf-8', errors='ignore')).hexdigest()[:32]


def _telethon_redis_lock_key(owner_id: int) -> str:
    """Ключ Redis pch:telethon:sess:{hash} (режим TELETHON_SESSION_LOCK_BACKEND=redis)."""
    return f'pch:telethon:sess:{_telethon_sess_hash(owner_id)}'


def _telethon_flock_lock_path(owner_id: int) -> Path:
    """Файл advisory lock рядом с сессиями (режим file / both)."""
    from django.conf import settings

    base = Path(settings.BASE_DIR) / 'media' / 'telethon_sessions'
    h = _telethon_sess_hash(owner_id)
    return base / '.flocks' / f'{h}.lock'


_REDIS_UNLOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""

_REDIS_EXTEND_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("expire", KEYS[1], tonumber(ARGV[2]))
else
  return 0
end
"""


def _wait_tick_maybe(
    on_lock_wait_tick: Optional[Callable[[float], None]],
    wait_start: float,
    last_tick_ref: list[float],
    now: float,
) -> None:
    if not on_lock_wait_tick:
        return
    elapsed = time.monotonic() - wait_start
    if elapsed >= 15 and (now - last_tick_ref[0]) >= 25:
        try:
            on_lock_wait_tick(elapsed)
        except Exception:
            pass
        last_tick_ref[0] = time.monotonic()


@contextmanager
def _telethon_session_lock_fcntl_impl(
    owner_id: int,
    on_lock_wait_tick: Optional[Callable[[float], None]],
    wait: float,
    wait_chunk: float,
):
    import fcntl

    path = _telethon_flock_lock_path(owner_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(path, 'a+b')
    acquired = False
    wait_start = time.monotonic()
    deadline = wait_start + wait
    poll_interval = max(0.25, min(4.0, float(wait_chunk)))
    last_tick: list[float] = [wait_start]
    try:
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                break
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                pass
            except OSError as exc:
                logger.warning('Telethon flock OSError owner_id=%s path=%s: %s', owner_id, path, exc)
            _wait_tick_maybe(on_lock_wait_tick, wait_start, last_tick, now)
            time.sleep(min(poll_interval, max(remaining, 0.05)))
        if not acquired:
            raise RuntimeError(
                f'Парсинг Telegram: не удалось занять файловый lock сессии за {int(wait)} с '
                '(другой процесс на этом же сервере держит тот же Telethon session).'
            )
        waited = time.monotonic() - wait_start
        if waited > 2:
            logger.info('Telethon flock acquired owner_id=%s path=%s after %.1fs', owner_id, path, waited)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        try:
            fp.close()
        except Exception:
            pass


@contextmanager
def _telethon_session_lock_redis_impl(
    owner_id: int,
    on_lock_wait_tick: Optional[Callable[[float], None]],
    wait: float,
    wait_chunk: float,
    hold: int,
):
    from django.conf import settings

    try:
        import redis
    except ImportError:
        yield
        return

    url = getattr(settings, 'DJANGO_CACHE_REDIS_URL', None) or ''
    if not url:
        yield
        return

    lock_name = _telethon_redis_lock_key(owner_id)
    token = secrets.token_hex(20)
    r = redis.from_url(
        url,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
    acquired = False
    wait_start = time.monotonic()
    deadline = wait_start + wait
    poll_interval = max(0.25, min(4.0, float(wait_chunk)))
    last_tick: list[float] = [wait_start]
    renew_interval = getattr(settings, 'TELETHON_REDIS_LOCK_RENEW_INTERVAL', None)
    if renew_interval is None:
        renew_interval = max(20.0, min(float(hold) * 0.28, 300.0))
    else:
        renew_interval = float(renew_interval)
    stop_renew = threading.Event()
    renew_thread: Optional[threading.Thread] = None

    def _renew_loop():
        """Продлевает TTL, пока воркер жив; после kill -9 ключ сам истечёт (см. TELETHON_REDIS_LOCK_TTL)."""
        try:
            r_local = redis.from_url(
                url,
                socket_connect_timeout=5,
                socket_timeout=5,
                health_check_interval=30,
            )
        except Exception as exc:
            logger.warning('Telethon lock renew: redis connect owner_id=%s: %s', owner_id, exc)
            return
        hold_sec = int(hold)
        while not stop_renew.wait(timeout=renew_interval):
            try:
                n = r_local.eval(_REDIS_EXTEND_LUA, 1, lock_name, token, str(hold_sec))
                if not n:
                    break
            except Exception as exc:
                logger.warning(
                    'Telethon lock renew failed owner_id=%s key=%s: %s',
                    owner_id,
                    lock_name,
                    exc,
                )

    try:
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                break
            try:
                acquired = bool(r.set(lock_name, token, nx=True, ex=int(hold)))
            except redis.RedisError as exc:
                logger.warning(
                    'Telethon Redis SET NX error owner_id=%s key=%s: %s',
                    owner_id,
                    lock_name,
                    exc,
                )
                acquired = False
            except Exception as exc:
                logger.warning(
                    'Telethon Redis SET NX unexpected owner_id=%s key=%s: %s',
                    owner_id,
                    lock_name,
                    exc,
                )
                acquired = False
            if acquired:
                break
            _wait_tick_maybe(on_lock_wait_tick, wait_start, last_tick, now)
            time.sleep(min(poll_interval, max(remaining, 0.05)))
        if not acquired:
            raise RuntimeError(
                f'Парсинг Telegram: не удалось занять сессию в Redis за {int(wait)} с '
                '(параллельно идёт другой импорт истории или парсинг этого же файла сессии).'
            )
        waited = time.monotonic() - wait_start
        if waited > 2:
            logger.info(
                'Telethon Redis lock acquired owner_id=%s key=%s after %.1fs',
                owner_id,
                lock_name,
                waited,
            )
        renew_thread = threading.Thread(
            target=_renew_loop,
            name=f'telethon-lock-renew-{owner_id}',
            daemon=True,
        )
        renew_thread.start()
        yield
    finally:
        stop_renew.set()
        if renew_thread is not None and renew_thread.is_alive():
            renew_thread.join(timeout=3.0)
        if acquired:
            try:
                r.eval(_REDIS_UNLOCK_LUA, 1, lock_name, token)
            except Exception:
                pass


@contextmanager
def _telethon_session_lock(
    owner_id: int,
    on_lock_wait_tick: Optional[Callable[[float], None]] = None,
    *,
    wait: Optional[float] = None,
    wait_chunk: Optional[float] = None,
):
    """
    Один процесс Celery на один файл сессии Telethon: иначе SQLite database is locked
    и при loop.close() рвутся фоновые задачи MTProto.

    По умолчанию TELETHON_SESSION_LOCK_BACKEND=file — fcntl на общем volume (Docker).
    Режим redis: SET NX с TELETHON_REDIS_LOCK_TTL и фоновым продлением, пока держатель жив;
    после kill -9 воркера ключ сам истечёт (не держать TTL сутками).

    wait / wait_chunk — переопределение таймаутов ожидания (например короткое ожидание в веб-запросе).
    """
    from django.conf import settings

    hold = int(getattr(settings, 'TELETHON_REDIS_LOCK_TTL', 28800))
    wait = float(wait if wait is not None else getattr(settings, 'TELETHON_REDIS_LOCK_WAIT', 600))
    wait_chunk = float(
        wait_chunk if wait_chunk is not None else getattr(settings, 'TELETHON_REDIS_LOCK_WAIT_CHUNK', 30)
    )
    backend = (getattr(settings, 'TELETHON_SESSION_LOCK_BACKEND', 'file') or 'file').strip().lower()

    if backend not in ('file', 'redis', 'both'):
        logger.warning('TELETHON_SESSION_LOCK_BACKEND=%r неизвестен, использую file', backend)
        backend = 'file'

    if backend == 'redis':
        with _telethon_session_lock_redis_impl(owner_id, on_lock_wait_tick, wait, wait_chunk, hold):
            yield
        return

    if backend == 'both':
        with _telethon_session_lock_fcntl_impl(owner_id, on_lock_wait_tick, wait, wait_chunk):
            with _telethon_session_lock_redis_impl(owner_id, None, wait, wait_chunk, hold):
                yield
        return

    try:
        import fcntl  # noqa: F401
    except ImportError:
        logger.warning('fcntl недоступен — Telethon lock только через Redis')
        with _telethon_session_lock_redis_impl(owner_id, on_lock_wait_tick, wait, wait_chunk, hold):
            yield
        return

    with _telethon_session_lock_fcntl_impl(owner_id, on_lock_wait_tick, wait, wait_chunk):
        yield


def telethon_session_lock_redis_status(owner_id: int) -> dict:
    """
    Диагностика: Redis-ключ, при file — путь flock и проба LOCK_NB.
    """
    from django.conf import settings

    backend = (getattr(settings, 'TELETHON_SESSION_LOCK_BACKEND', 'file') or 'file').strip().lower()
    key = _telethon_redis_lock_key(int(owner_id))
    flock_path = _telethon_flock_lock_path(int(owner_id))
    out: dict = {
        'owner_id': int(owner_id),
        'lock_backend': backend,
        'lock_key': key,
        'flock_path': str(flock_path),
        'held_in_redis': None,
        'flock_held_probe': None,
        'ttl_sec': None,
        'error': None,
    }

    try:
        import redis
    except ImportError:
        out['redis_error'] = 'redis не установлен'
    else:
        url = getattr(settings, 'DJANGO_CACHE_REDIS_URL', None) or ''
        if not url:
            out['redis_error'] = 'DJANGO_CACHE_REDIS_URL пуст'
        else:
            try:
                r = redis.from_url(
                    url,
                    socket_connect_timeout=3,
                    socket_timeout=3,
                    health_check_interval=30,
                )
                if r.exists(key):
                    out['held_in_redis'] = True
                    try:
                        t = r.ttl(key)
                        out['ttl_sec'] = int(t) if t is not None and int(t) > 0 else None
                    except Exception:
                        pass
                else:
                    out['held_in_redis'] = False
            except Exception as exc:
                out['redis_error'] = str(exc)

    try:
        import fcntl

        if not flock_path.exists():
            out['flock_held_probe'] = False
        else:
            fp = open(flock_path, 'a+b')
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
                out['flock_held_probe'] = False
            except BlockingIOError:
                out['flock_held_probe'] = True
            finally:
                fp.close()
    except ImportError:
        out['flock_held_probe'] = None
        out['flock_probe_error'] = 'fcntl недоступен'
    except Exception as exc:
        out['flock_probe_error'] = str(exc)

    return out


# ─── AI рерайт ───────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3)
def ai_rewrite_task(self, job_id: int):
    """Генерация/рерайт текста через DeepSeek API."""
    from .models import AIRewriteJob
    try:
        job = AIRewriteJob.objects.get(pk=job_id)
    except AIRewriteJob.DoesNotExist:
        return

    job.status = AIRewriteJob.STATUS_PROCESSING
    job.save(update_fields=['status'])

    try:
        from core.models import get_global_api_keys
        keys = get_global_api_keys()

        api_key = keys.get_deepseek_api_key()
        if not api_key:
            job.status = AIRewriteJob.STATUS_FAILED
            job.error = 'Ключ DeepSeek не задан (Ключи API → DeepSeek).'
            job.save(update_fields=['status', 'error'])
            return
        if not keys.ai_rewrite_enabled:
            job.status = AIRewriteJob.STATUS_FAILED
            job.error = 'Включите «AI рерайт» в разделе «Ключи API».'
            job.save(update_fields=['status', 'error'])
            return

        from django.conf import settings

        from .deepseek_snippet import build_deepseek_client

        client = build_deepseek_client(api_key)

        prompt = job.prompt or (
            'Перепиши следующий текст в нейтральном информационном стиле, '
            'сохранив смысл. Текст должен быть лаконичным и подходить для '
            'публикации в социальных сетях. Верни только переписанный текст.'
        )

        response = client.chat.completions.create(
            model=job.model_name or getattr(settings, 'DEEPSEEK_MODEL', 'deepseek-chat'),
            messages=[
                {'role': 'system', 'content': prompt},
                {'role': 'user', 'content': job.original_text},
            ],
            max_tokens=2000,
        )
        job.result_text = response.choices[0].message.content.strip()
        job.status = AIRewriteJob.STATUS_DONE
        job.completed_at = timezone.now()
        job.save()
        logger.info(f'AI рерайт #{job_id} завершён')

    except Exception as exc:
        job.status = AIRewriteJob.STATUS_FAILED
        job.error = str(exc)
        job.save(update_fields=['status', 'error'])
        logger.error(f'Ошибка AI рерайта #{job_id}: {exc}')
        return


# ─── Парсинг контента ────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    soft_time_limit=1200,
    time_limit=1260,
)
def execute_parse_task(self, task_id: int):
    """Выполняет задачу парсинга: обходит все источники, ищет по ключевикам."""
    from django.db.models import Max

    from .models import ParseSource, ParseTask, ParsedItem
    try:
        task = ParseTask.objects.prefetch_related('sources', 'keywords').get(pk=task_id)
    except ParseTask.DoesNotExist:
        logger.error(f'ParseTask #{task_id} не найдена')
        return

    # Сразу отмечаем «запуск», иначе check_parse_tasks (beat каждые ~5 мин) видит старый last_run_at,
    # пока долгий Telethon ещё выполняется, и снова ставит execute_parse_task в очередь — parse раздувается.
    ParseTask.objects.filter(pk=task_id).update(last_run_at=timezone.now())

    keywords = list(task.keywords.filter(is_active=True).values_list('keyword', flat=True))
    keyword_objects = {kw.keyword: kw for kw in task.keywords.filter(is_active=True)}

    if not keywords:
        logger.info(f'ParseTask #{task_id}: нет активных ключевых слов, пропуск')
        ParseTask.objects.filter(pk=task_id).update(last_run_at=timezone.now())
        return

    total_found = 0

    logger.info('ParseTask #%s старт: sources=%s keywords=%s', task_id, task.sources.count(), task.keywords.filter(is_active=True).count())

    try:
        for source in task.sources.filter(is_active=True):
            max_id_before = ParsedItem.objects.filter(source=source).aggregate(m=Max('pk'))['m'] or 0
            try:
                logger.info(
                    'ParseTask #%s: source #%s %s platform=%s source_id=%s',
                    task_id,
                    source.pk,
                    source.name,
                    source.platform,
                    (source.source_id or '')[:120],
                )
                if source.platform == source.PLATFORM_TELEGRAM:
                    found = _parse_telegram(source, keywords, keyword_objects)
                elif source.platform == source.PLATFORM_VK:
                    found = _parse_vk(source, keywords, keyword_objects)
                elif source.platform == source.PLATFORM_RSS:
                    found = _parse_rss(source, keywords, keyword_objects)
                elif source.platform == source.PLATFORM_DZEN:
                    found = _parse_dzen(source, keywords, keyword_objects)
                else:
                    logger.warning(f'Неизвестная платформа: {source.platform}')
                    found = 0

                new_qs = ParsedItem.objects.filter(source=source, pk__gt=max_id_before)
                ParseSource.objects.filter(pk=source.pk).update(
                    last_parsed_at=timezone.now(),
                    last_parse_new_items=new_qs.count(),
                    last_parse_keywords_matched=new_qs.values('keyword_id').distinct().count(),
                )

                total_found += found
                logger.info(f'ParseTask #{task_id}, источник "{source.name}": найдено {found}')
            except Exception as exc:
                logger.exception('Ошибка парсинга источника "%s" (%s): %s', source.name, source.platform, exc)
                ParseSource.objects.filter(pk=source.pk).update(last_parsed_at=timezone.now())
                # Audit log (best-effort): сохраняем ошибки парсинга в журнал действий
                try:
                    from bots.models import AuditLog

                    tb = traceback.format_exc()
                    if len(tb) > 8000:
                        tb = tb[:8000] + "\n…(truncated)…"
                    AuditLog.objects.create(
                        actor=None,
                        owner=task.owner,
                        action='parsing.error',
                        object_type='ParseSource',
                        object_id=str(source.pk),
                        data={
                            'task_id': task.pk,
                            'task_name': task.name,
                            'source_id': source.pk,
                            'source_name': source.name,
                            'source_platform': source.platform,
                            'source_source_id': (source.source_id or '')[:500],
                            'error': str(exc)[:1000],
                            'traceback': tb,
                        },
                    )
                except Exception:
                    pass

        task.last_run_at = timezone.now()
        task.items_found_total += total_found
        task.save(update_fields=['last_run_at', 'items_found_total'])

        logger.info(f'ParseTask #{task_id} завершена: найдено {total_found} новых материалов')
        return total_found
    except SoftTimeLimitExceeded:
        from django.db.models import F

        logger.error(
            'ParseTask #%s: soft time limit (20 мин) — прервано, освобождаем воркер. '
            'Уменьшите источники или проверьте сеть/Telethon.',
            task_id,
        )
        ParseTask.objects.filter(pk=task_id).update(
            last_run_at=timezone.now(),
            items_found_total=F('items_found_total') + total_found,
        )
        raise


def _match_keywords(text, keywords):
    """Возвращает список ключевых слов, найденных в тексте (регистронезависимо)."""
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]


def _save_item(
    source,
    keyword_obj,
    text,
    platform_id,
    original_url='',
    media=None,
    source_posted_at=None,
):
    """Сохраняет найденный материал, если его ещё нет (дедупликация по source + platform_id)."""
    from .models import ParsedItem

    if not platform_id:
        return False
    if media is None:
        media = []
    _, created = ParsedItem.objects.get_or_create(
        source=source,
        platform_id=str(platform_id),
        defaults={
            'keyword': keyword_obj,
            'text': text[:10000],
            'original_url': original_url[:200] if original_url else '',
            'media': media,
            'source_posted_at': source_posted_at,
        },
    )
    return created


# ─── Telegram (через Telethon) ───────────────────────────────────────────────

def _parse_telegram(source, keywords, keyword_objects):
    """Парсинг Telegram-канала через Telethon (user API).

    Требует TELEGRAM_API_ID и TELEGRAM_API_HASH в settings.
    source.source_id — имя канала (@channel) или числовой ID.
    """
    from django.conf import settings
    from core.models import get_global_api_keys
    from .models import ParsedItem
    keys = get_global_api_keys()
    api_id = (keys.telegram_api_id or '').strip()
    api_hash = (keys.get_telegram_api_hash() or '').strip()
    if not api_id or not api_hash:
        raise ValueError('TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы (Ключи API → Парсинг Telegram).')

    async def _fetch():
        from telethon import TelegramClient
        from pathlib import Path
        from django.conf import settings
        # Session is per-owner, created via interactive web flow or management command.
        session_dir = settings.BASE_DIR / 'media' / 'telethon_sessions'
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        session_path = str(session_dir / f'user_{source.owner_id}')
        _tc_kw = _telethon_client_kwargs()
        logger.info('TG parse: owner_id=%s session=%s', source.owner_id, session_path)
        client = None
        try:
            client = TelegramClient(session_path, int(api_id), api_hash, **_tc_kw)
            await client.connect()
            if not await client.is_user_authorized():
                # Fallback: если авторизация делалась через management command telethon_login,
                # то session хранится как user_default.session
                try:
                    await client.disconnect()
                except Exception:
                    pass
                client = None
                default_path = str(session_dir / 'user_default')
                logger.info('TG parse: fallback session=%s', default_path)
                client = TelegramClient(default_path, int(api_id), api_hash, **_tc_kw)
                await client.connect()
                if not await client.is_user_authorized():
                    raise ValueError(
                        'Telethon session не авторизована. '
                        'Подключите Telegram в UI (Парсинг → Подключить Telegram) или выполните '
                        '`python manage.py telethon_login` в контейнере web. '
                        'Важно: у celery должен быть смонтирован тот же /app/media.'
                    )
            found = 0
            msg_limit = int(getattr(settings, 'PARSE_TELEGRAM_MESSAGE_LIMIT', 20) or 20)
            msg_limit = max(5, min(msg_limit, 500))
            scanned_with_text = 0
            keyword_hits = 0
            logger.info('TG parse: get_entity source_id=%s limit=%s', (source.source_id or '')[:120], msg_limit)
            channel = await client.get_entity(source.source_id)
            # Последние N сообщений (новые к моменту запуска уже могут быть в БД → «найдено 0», см. лог duplicate).
            async for message in client.iter_messages(channel, limit=msg_limit):
                msg_text = ''
                try:
                    msg_text = (message.text or '').strip()
                except Exception:
                    msg_text = ''
                if not msg_text:
                    try:
                        raw_txt = getattr(message, 'raw_text', None)
                        if raw_txt is not None:
                            msg_text = str(raw_txt).strip()
                    except Exception:
                        pass
                if not msg_text:
                    try:
                        poll = getattr(message, 'poll', None)
                        if poll is not None:
                            pq = getattr(poll, 'poll', poll)
                            q = getattr(pq, 'question', None) or getattr(poll, 'question', None)
                            if q:
                                msg_text = str(q).strip()
                    except Exception:
                        pass
                if not msg_text:
                    continue
                scanned_with_text += 1
                matched = _match_keywords(msg_text, keywords)
                if matched:
                    keyword_hits += 1
                    kw_obj = keyword_objects[matched[0]]
                    # Уже есть в БД — не качаем медиа повторно (иначе каждый запуск парсинга раздувает диск).
                    already_stored = await sync_to_async(
                        lambda mid=message.id: ParsedItem.objects.filter(
                            source=source,
                            platform_id=str(mid),
                        ).exists()
                    )()
                    if already_stored:
                        logger.info(
                            'TG parse: пропуск (уже в БД, без повторного скачивания медиа) source=%s msg_id=%s',
                            source.pk,
                            getattr(message, 'id', None),
                        )
                        continue
                    # Публичная ссылка на оригинальный пост (если source_id публичный).
                    original_url = ''
                    try:
                        sid = (source.source_id or '').strip()
                        username = ''
                        if sid.startswith('@'):
                            username = sid.lstrip('@').strip()
                        elif 't.me/' in sid:
                            # https://t.me/<username> or https://t.me/<username>/...
                            after = sid.split('t.me/', 1)[1]
                            username = after.split('?', 1)[0].split('#', 1)[0].strip('/').split('/', 1)[0]
                        if username and getattr(message, 'id', None):
                            original_url = f'https://t.me/{username}/{message.id}'
                    except Exception:
                        original_url = ''

                    media_urls = []
                    try:
                        if getattr(message, "media", None):
                            media_root = Path(getattr(settings, "MEDIA_ROOT", "media"))
                            rel_dir = Path("parsed_items") / "telegram" / f"source_{source.pk}"
                            abs_dir = media_root / rel_dir
                            abs_dir.mkdir(parents=True, exist_ok=True)
                            base = abs_dir / f"msg_{message.id}"
                            skip_download = False
                            max_bytes = int(
                                getattr(settings, 'PARSE_TELEGRAM_MEDIA_MAX_BYTES', 0) or 0
                            )
                            if max_bytes > 0:
                                doc = getattr(message, 'document', None)
                                sz = int(getattr(doc, 'size', 0) or 0) if doc is not None else 0
                                if doc is not None and sz > max_bytes:
                                    logger.info(
                                        'TG parse: медиа пропущено (документ %s байт > лимита %s) source=%s msg_id=%s',
                                        sz,
                                        max_bytes,
                                        source.pk,
                                        getattr(message, 'id', None),
                                    )
                                    skip_download = True
                            if not skip_download:
                                saved_path = await client.download_media(message, file=str(base))
                                if saved_path:
                                    p = Path(saved_path)
                                    try:
                                        rel = p.relative_to(media_root)
                                        media_urls = ["/media/" + str(rel).replace("\\", "/")]
                                    except Exception:
                                        media_urls = []
                    except Exception:
                        media_urls = []

                    posted_at = None
                    try:
                        md = getattr(message, 'date', None)
                        if md is not None:
                            posted_at = timezone.make_aware(md) if timezone.is_naive(md) else md
                    except Exception:
                        posted_at = None

                    created = await sync_to_async(_save_item, thread_sensitive=True)(
                        source, kw_obj, msg_text, str(message.id), original_url, media_urls, posted_at
                    )
                    if created:
                        found += 1
                    else:
                        logger.info(
                            'TG parse: duplicate skip source=%s msg_id=%s (уже в ленте парсинга)',
                            source.pk,
                            getattr(message, 'id', None),
                        )
            logger.info(
                'TG parse: source=%s done with_text=%s keyword_hits=%s new_saved=%s',
                source.pk,
                scanned_with_text,
                keyword_hits,
                found,
            )
            return found
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception as ex:
                    logger.warning('TG parse: disconnect: %s', ex)

    # asyncio.run: корректное завершение цикла (ручной loop.close() рвал фоновые задачи Telethon).
    # Один lock на файл сессии с импортом TG→MAX: пока этот блок выполняется, import_tg_history_to_max_task
    # на шаге 4 ждёт в Redis (см. лог «lock acquired» ниже и «history_import» в channels.tasks).
    logger.info(
        'TG parse: ожидание lock сессии Telethon owner_id=%s source_id=%s (тот же lock, что у импорта истории)',
        source.owner_id,
        source.pk,
    )
    with _telethon_session_lock(source.owner_id):
        logger.info(
            'TG parse: lock получен, старт Telethon owner_id=%s source_id=%s',
            source.owner_id,
            source.pk,
        )
        try:
            return _telethon_asyncio_run(_fetch())
        finally:
            logger.info(
                'TG parse: завершён проход Telethon, lock будет снят owner_id=%s source_id=%s',
                source.owner_id,
                source.pk,
            )


# ─── VK (через vk_api) ──────────────────────────────────────────────────────

def _parse_vk(source, keywords, keyword_objects):
    """Парсинг VK группы/паблика.

    source.source_id — числовой ID группы (club12345) или домен.
    Использует токен из settings.VK_PARSE_ACCESS_TOKEN или токен сервиса.
    """
    import vk_api
    from core.models import get_global_api_keys
    keys = get_global_api_keys()
    token = (keys.get_vk_parse_access_token() or '').strip()
    if not token:
        raise ValueError('VK_PARSE_ACCESS_TOKEN не задан (Ключи API → Парсинг VK).')

    session = vk_api.VkApi(token=token)
    vk = session.get_api()

    # Определяем owner_id из source_id
    source_id = source.source_id.strip()
    if source_id.startswith('-'):
        owner_id = int(source_id)
    elif source_id.isdigit():
        owner_id = -int(source_id)
    elif source_id.startswith('club'):
        owner_id = -int(source_id.replace('club', ''))
    else:
        # Пробуем как короткое имя
        try:
            resolved = vk.utils.resolveScreenName(screen_name=source_id.lstrip('@'))
            if resolved and resolved.get('type') in ('group', 'page'):
                owner_id = -resolved['object_id']
            else:
                logger.warning(f'VK: не удалось определить ID для "{source_id}"')
                return 0
        except Exception as exc:
            logger.error(f'VK resolveScreenName ошибка: {exc}')
            return 0

    found = 0
    try:
        # Берём только последние 20 постов, чтобы не упереться в лимиты API
        posts = vk.wall.get(owner_id=owner_id, count=20, filter='owner')
        for item in posts.get('items', []):
            text = item.get('text', '')
            if not text:
                continue
            matched = _match_keywords(text, keywords)
            if matched:
                kw_obj = keyword_objects[matched[0]]
                post_id = item.get('id', '')
                url = f'https://vk.com/wall{owner_id}_{post_id}'
                posted_at = None
                ts = item.get('date')
                if ts:
                    try:
                        from datetime import datetime, timezone as dt_tz

                        posted_at = datetime.fromtimestamp(int(ts), tz=dt_tz.utc)
                    except Exception:
                        posted_at = None
                if _save_item(source, kw_obj, text, f'{owner_id}_{post_id}', url, None, posted_at):
                    found += 1
    except Exception as exc:
        logger.error(f'VK wall.get ошибка для {source_id}: {exc}')

    return found


# ─── RSS (через feedparser) ─────────────────────────────────────────────────

def _parse_rss(source, keywords, keyword_objects):
    """Парсинг RSS-ленты.

    source.source_id — URL RSS-фида.
    """
    import feedparser

    feed = feedparser.parse(source.source_id)
    found = 0

    # Берём только последние 20 записей, чтобы не подтягивать давний контент.
    for entry in feed.entries[:20]:
        title = entry.get('title', '')
        summary = entry.get('summary', '')
        text = f'{title}\n\n{summary}' if summary else title
        if not text.strip():
            continue

        matched = _match_keywords(text, keywords)
        if matched:
            kw_obj = keyword_objects[matched[0]]
            entry_id = entry.get('id', '') or entry.get('link', '')
            link = entry.get('link', '')
            posted_at = None
            pp = entry.get('published_parsed') or entry.get('updated_parsed')
            if pp:
                try:
                    from datetime import datetime, timezone as dt_tz

                    posted_at = datetime(
                        pp.tm_year,
                        pp.tm_mon,
                        pp.tm_mday,
                        pp.tm_hour,
                        pp.tm_min,
                        pp.tm_sec,
                        tzinfo=dt_tz.utc,
                    )
                except Exception:
                    posted_at = None
            if _save_item(source, kw_obj, text, entry_id, link, None, posted_at):
                found += 1

    return found


# ─── Яндекс Дзен (через requests + BS4) ─────────────────────────────────────

def _parse_dzen(source, keywords, keyword_objects):
    """Парсинг Яндекс Дзен канала.

    source.source_id — URL канала на Дзен (https://dzen.ru/...).
    Парсим HTML страницы канала и извлекаем заголовки/анонсы.
    """
    import requests
    from bs4 import BeautifulSoup

    url = source.source_id.strip()
    if not url.startswith('http'):
        url = f'https://dzen.ru/{url}'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        logger.error(f'Дзен: ошибка загрузки {url}: {exc}')
        return 0

    soup = BeautifulSoup(resp.text, 'lxml')
    found = 0

    # Ищем карточки статей по типичным CSS-классам Дзен
    articles = soup.select('article, [class*="card"], [class*="feed-item"], [class*="article"]')

    # Берём только последние 20 карточек, чтобы не подтягивать давний контент.
    for article in articles[:20]:
        title_el = article.find(['h2', 'h3', 'h4', 'a'])
        snippet_el = article.find(['p', 'span'])
        title = title_el.get_text(strip=True) if title_el else ''
        snippet = snippet_el.get_text(strip=True) if snippet_el else ''
        text = f'{title}\n\n{snippet}' if snippet else title
        if not text.strip():
            continue

        matched = _match_keywords(text, keywords)
        if matched:
            kw_obj = keyword_objects[matched[0]]
            link_el = article.find('a', href=True)
            link = link_el['href'] if link_el else ''
            if link and not link.startswith('http'):
                link = f'https://dzen.ru{link}'
            # Используем ссылку как platform_id для дедупликации
            platform_id = link or title[:100]
            if _save_item(source, kw_obj, text, platform_id, link):
                found += 1

    return found


# ─── Периодическая задача ────────────────────────────────────────────────────

@shared_task
def check_parse_tasks():
    """Проверяет активные задачи парсинга и запускает те, которые пора выполнять.

    Сравнивает текущее время с cron-расписанием задачи.
    Простая логика: если с последнего запуска прошло достаточно времени — запускаем.
    """
    from .models import ParseTask
    from datetime import timedelta

    now = timezone.now()
    active_tasks = ParseTask.objects.filter(is_active=True)

    count = 0
    for task in active_tasks:
        interval = _cron_to_interval(task.schedule_cron)
        if task.last_run_at and (now - task.last_run_at) < interval:
            continue

        execute_parse_task.delay(task.pk)
        count += 1
        logger.info(f'Запущена задача парсинга #{task.pk}: {task.name}')

    if count:
        logger.info(f'Запущено {count} задач парсинга')
    return count


def _cron_to_interval(cron_expr):
    """Конвертирует простые cron-выражения в timedelta.

    Поддерживает:
    - */N в поле часов → каждые N часов
    - */N в поле минут → каждые N минут
    - Фоллбэк: 6 часов
    """
    from datetime import timedelta
    parts = cron_expr.strip().split()
    if len(parts) < 5:
        return timedelta(hours=6)

    minute, hour = parts[0], parts[1]

    # Каждые N часов: 0 */N * * *
    match = re.match(r'\*/(\d+)', hour)
    if match:
        return timedelta(hours=int(match.group(1)))

    # Каждые N минут: */N * * * *
    match = re.match(r'\*/(\d+)', minute)
    if match:
        return timedelta(minutes=int(match.group(1)))

    return timedelta(hours=6)


@shared_task(ignore_result=True)
def purge_parse_media_retention():
    """Очистка парсинга по сроку и квоте (GlobalApiKeys; запасной вариант — .env)."""
    from core.models import effective_parse_media_retention_days
    from parsing.media_retention import run_parse_media_cleanup

    return run_parse_media_cleanup(retention_days=effective_parse_media_retention_days())


def _harvest_message_text(message) -> str:
    msg_text = ''
    try:
        msg_text = (message.text or '').strip()
    except Exception:
        msg_text = ''
    if not msg_text:
        try:
            raw_txt = getattr(message, 'raw_text', None)
            if raw_txt is not None:
                msg_text = str(raw_txt).strip()
        except Exception:
            pass
    if not msg_text:
        try:
            poll = getattr(message, 'poll', None)
            if poll is not None:
                pq = getattr(poll, 'poll', poll)
                q = getattr(pq, 'question', None) or getattr(poll, 'question', None)
                if q:
                    msg_text = str(q).strip()
        except Exception:
            pass
    return msg_text


def _run_harvest_telethon_fetch_multi(owner_id: int, channel_refs: list[str], limit_per_channel: int) -> list[dict]:
    """
    Последние посты с текстом из нескольких публичных TG-каналов (одна сессия Telethon).
    Каждый элемент: id, text, channel_ref.
    """
    from pathlib import Path

    from django.conf import settings

    from core.models import get_global_api_keys

    seen: set[str] = set()
    uniq_refs: list[str] = []
    for r in channel_refs or []:
        r = (r or '').strip()
        if not r:
            continue
        k = r.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq_refs.append(r)
    if not uniq_refs:
        return []

    keys = get_global_api_keys()
    api_id = (keys.telegram_api_id or '').strip()
    api_hash = (keys.get_telegram_api_hash() or '').strip()
    if not api_id or not api_hash:
        raise ValueError('TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы (Ключи API → Парсинг Telegram).')

    lim = max(1, min(int(limit_per_channel), 65535))

    async def _fetch():
        from telethon import TelegramClient

        _tc_kw = _telethon_client_kwargs()
        session_dir = Path(settings.BASE_DIR) / 'media' / 'telethon_sessions'
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = str(session_dir / f'user_{int(owner_id)}')
        client = TelegramClient(session_path, int(api_id), api_hash, **_tc_kw)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            client = TelegramClient(str(session_dir / 'user_default'), int(api_id), api_hash, **_tc_kw)
            await client.connect()
            if not await client.is_user_authorized():
                raise ValueError(
                    'Telethon не авторизован для владельца аккаунта. '
                    'Владелец должен подключить Telegram в разделе «Парсинг».'
                )
        collected: list[dict] = []
        try:
            for channel_ref in uniq_refs:
                try:
                    entity = await client.get_entity(channel_ref)
                except Exception as exc:
                    logger.warning('harvest TG: get_entity %s: %s', channel_ref, exc)
                    continue
                async for message in client.iter_messages(entity, limit=lim):
                    msg_text = _harvest_message_text(message)
                    if not msg_text:
                        continue
                    try:
                        mid = int(message.id)
                    except Exception:
                        continue
                    collected.append({'id': mid, 'text': msg_text, 'channel_ref': channel_ref})
        finally:
            try:
                await client.disconnect()
            except Exception as ex:
                logger.warning('harvest TG: disconnect: %s', ex)
        return collected

    with _telethon_session_lock(int(owner_id)):
        return _telethon_asyncio_run(_fetch())


def _run_harvest_telethon_fetch(owner_id: int, channel_ref: str, limit: int) -> list[dict]:
    """Один канал; совместимость с прежним форматом (без channel_ref в элементах)."""
    rows = _run_harvest_telethon_fetch_multi(owner_id, [channel_ref], limit)
    for r in rows:
        r.pop('channel_ref', None)
    return rows


@shared_task(soft_time_limit=900, time_limit=960)
def run_keyword_harvest_job(job_id: int):
    """Очередь: выгрузка постов примеров → DeepSeek → suggested_keywords, статус ready."""
    from .harvest_services import extract_ranked_keywords_with_deepseek, harvest_example_channel_refs
    from .models import KeywordHarvestJob

    try:
        job = KeywordHarvestJob.objects.select_related('channel_group', 'target_channel').get(pk=job_id)
    except KeywordHarvestJob.DoesNotExist:
        return

    if job.status != KeywordHarvestJob.STATUS_PENDING:
        return

    job.status = KeywordHarvestJob.STATUS_RUNNING
    job.error_message = ''
    job.save(update_fields=['status', 'error_message', 'updated_at'])

    refs = harvest_example_channel_refs(job)
    if not refs:
        job.status = KeywordHarvestJob.STATUS_FAILED
        job.error_message = 'Не указан ни один канал-пример.'
        job.save(update_fields=['status', 'error_message', 'updated_at'])
        return

    owner_id = job.channel_group.owner_id
    lim = max(5, min(int(job.max_posts or 20), 65535))

    try:
        try:
            posts = _run_harvest_telethon_fetch_multi(owner_id, refs, lim)
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            logger.exception('keyword harvest fetch job_id=%s', job_id)
            job.status = KeywordHarvestJob.STATUS_FAILED
            job.error_message = str(exc)[:2000]
            job.save(update_fields=['status', 'error_message', 'updated_at'])
            return

        if not posts:
            job.status = KeywordHarvestJob.STATUS_FAILED
            job.error_message = (
                'Не удалось получить текст постов: каналы недоступны, нет текстовых сообщений в лимите '
                'или нет доступа. Проверьте @username и что аккаунт Telethon видит каналы.'
            )
            job.save(update_fields=['status', 'error_message', 'updated_at'])
            return

        digest_lines = []
        snapshot = []
        for i, p in enumerate(posts):
            tid = p.get('id')
            cref = (p.get('channel_ref') or '')[:120]
            txt = (p.get('text') or '')[:1500]
            digest_lines.append(f'--- Канал {cref} · пост #{i + 1} (id {tid}) ---\n{txt}')
            snapshot.append(
                {
                    'id': tid,
                    'channel': cref,
                    'snippet': (p.get('text') or '')[:400],
                }
            )
        combined = '\n\n'.join(digest_lines)

        from core.models import get_global_api_keys

        keys = get_global_api_keys()
        api_key = (keys.get_deepseek_api_key() or '').strip()
        job.posts_snapshot = snapshot[:2000]
        job.save(update_fields=['posts_snapshot', 'updated_at'])

        if not api_key:
            job.status = KeywordHarvestJob.STATUS_FAILED
            job.error_message = 'Не задан ключ DeepSeek (раздел «Ключи API»).'
            job.save(update_fields=['status', 'error_message', 'updated_at'])
            return

        try:
            kws = extract_ranked_keywords_with_deepseek(
                posts_digest_text=combined,
                region_prompt=job.region_prompt,
                api_key=api_key,
            )
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            logger.exception('keyword harvest deepseek job_id=%s', job_id)
            job.status = KeywordHarvestJob.STATUS_FAILED
            job.error_message = f'DeepSeek: {exc}'[:2000]
            job.save(update_fields=['status', 'error_message', 'updated_at'])
            return

        job.suggested_keywords = kws
        job.status = KeywordHarvestJob.STATUS_READY
        job.save(update_fields=['suggested_keywords', 'status', 'updated_at'])
    except SoftTimeLimitExceeded:
        msg = (
            'Превышено время выполнения задачи (лимит воркера, 15 мин). '
            'Часто из‑за очереди parse, ожидания блокировки Telethon (параллельный парсинг/импорт) '
            'или долгого ответа API. Повторите позже или проверьте логи воркера celery.'
        )
        try:
            j = KeywordHarvestJob.objects.get(pk=job_id)
            j.status = KeywordHarvestJob.STATUS_FAILED
            j.error_message = msg
            j.save(update_fields=['status', 'error_message', 'updated_at'])
        except Exception:
            logger.exception('keyword harvest soft limit: failed to mark job_id=%s failed', job_id)
