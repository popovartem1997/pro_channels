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
import hashlib
import logging
import re
import traceback
from contextlib import contextmanager
from pathlib import Path

from celery import shared_task
from django.utils import timezone
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


def _telethon_redis_lock_key(owner_id: int) -> str:
    """
    Ключ блокировки по фактическому файлу *.session, который выберет Telethon.

    Раньше был pch:telethon:owner:{id} — при общем user_default.session у разных владельцев
    получались разные ключи и параллельный доступ к одному SQLite → зависания и «вечное» ожидание.
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
    h = hashlib.sha256(token.encode('utf-8', errors='ignore')).hexdigest()[:32]
    return f'pch:telethon:sess:{h}'


@contextmanager
def _telethon_session_lock(owner_id: int):
    """
    Один воркер Celery на один файл сессии Telethon: иначе SQLite database is locked
    и при loop.close() рвутся фоновые задачи MTProto.
    """
    from django.conf import settings

    # TTL удержания: длинный импорт истории; при падении воркера ключ снимется сам.
    hold = int(getattr(settings, 'TELETHON_REDIS_LOCK_TTL', 28800))
    # Ожидание освобождения одной попыткой (сек.); было 120 — часто не хватало при фоновом импорте.
    wait = float(getattr(settings, 'TELETHON_REDIS_LOCK_WAIT', 600))

    try:
        import redis
    except ImportError:
        yield
        return

    url = getattr(settings, 'DJANGO_CACHE_REDIS_URL', None) or ''
    if not url:
        yield
        return

    r = redis.from_url(
        url,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
    lock_name = _telethon_redis_lock_key(owner_id)
    lock = r.lock(
        lock_name,
        timeout=hold,
        blocking_timeout=wait,
    )
    acquired = False
    try:
        acquired = lock.acquire(blocking=True, blocking_timeout=wait)
        if not acquired:
            raise RuntimeError(
                f'Парсинг Telegram: не удалось занять сессию за {int(wait)} с '
                '(параллельно идёт другой импорт истории или парсинг этого же файла сессии).'
            )
        yield
    finally:
        if acquired:
            try:
                lock.release()
            except Exception:
                pass


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

@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def execute_parse_task(self, task_id: int):
    """Выполняет задачу парсинга: обходит все источники, ищет по ключевикам."""
    from django.db.models import Max

    from .models import ParseSource, ParseTask, ParsedItem
    try:
        task = ParseTask.objects.prefetch_related('sources', 'keywords').get(pk=task_id)
    except ParseTask.DoesNotExist:
        logger.error(f'ParseTask #{task_id} не найдена')
        return

    keywords = list(task.keywords.filter(is_active=True).values_list('keyword', flat=True))
    keyword_objects = {kw.keyword: kw for kw in task.keywords.filter(is_active=True)}

    if not keywords:
        logger.info(f'ParseTask #{task_id}: нет активных ключевых слов, пропуск')
        ParseTask.objects.filter(pk=task_id).update(last_run_at=timezone.now())
        return

    total_found = 0

    logger.info('ParseTask #%s старт: sources=%s keywords=%s', task_id, task.sources.count(), task.keywords.filter(is_active=True).count())

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
    import asyncio
    from core.models import get_global_api_keys
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
        logger.info('TG parse: owner_id=%s session=%s', source.owner_id, session_path)
        client = None
        try:
            client = TelegramClient(session_path, int(api_id), api_hash)
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
                client = TelegramClient(default_path, int(api_id), api_hash)
                await client.connect()
                if not await client.is_user_authorized():
                    raise ValueError(
                        'Telethon session не авторизована. '
                        'Подключите Telegram в UI (Парсинг → Подключить Telegram) или выполните '
                        '`python manage.py telethon_login` в контейнере web. '
                        'Важно: у celery должен быть смонтирован тот же /app/media.'
                    )
            found = 0
            msg_limit = int(getattr(settings, 'PARSE_TELEGRAM_MESSAGE_LIMIT', 80) or 80)
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
                            # One file per message (enough for preview); telethon will choose proper extension.
                            base = abs_dir / f"msg_{message.id}"
                            saved_path = await client.download_media(message, file=str(base))
                            if saved_path:
                                p = Path(saved_path)
                                try:
                                    rel = p.relative_to(media_root)
                                    media_urls = ["/media/" + str(rel).replace("\\", "/")]
                                except Exception:
                                    # Fallback: absolute path is not web-accessible; skip
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
    with _telethon_session_lock(source.owner_id):
        return asyncio.run(_fetch())


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
