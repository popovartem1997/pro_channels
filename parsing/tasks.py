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
import logging
import re
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─── AI рерайт ───────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3)
def ai_rewrite_task(self, job_id: int):
    """Генерация/рерайт текста через OpenAI."""
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

        if not keys.ai_rewrite_enabled:
            job.status = AIRewriteJob.STATUS_FAILED
            job.error = 'AI рерайт временно отключён (заглушка).'
            job.save(update_fields=['status', 'error'])
            return
        api_key = keys.get_openai_api_key()
        if not api_key:
            raise ValueError('OPENAI_API_KEY не задан (Ключи API → OpenAI).')

        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        prompt = job.prompt or (
            'Перепиши следующий текст в нейтральном информационном стиле, '
            'сохранив смысл. Текст должен быть лаконичным и подходить для '
            'публикации в социальных сетях. Верни только переписанный текст.'
        )

        response = client.chat.completions.create(
            model=job.model_name or settings.OPENAI_MODEL,
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
        # Не ретраим, пока AI отключён / ключи могут отсутствовать
        return


# ─── Парсинг контента ────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def execute_parse_task(self, task_id: int):
    """Выполняет задачу парсинга: обходит все источники, ищет по ключевикам."""
    from .models import ParseTask
    try:
        task = ParseTask.objects.prefetch_related('sources', 'keywords').get(pk=task_id)
    except ParseTask.DoesNotExist:
        logger.error(f'ParseTask #{task_id} не найдена')
        return

    keywords = list(task.keywords.filter(is_active=True).values_list('keyword', flat=True))
    keyword_objects = {kw.keyword: kw for kw in task.keywords.filter(is_active=True)}

    if not keywords:
        logger.info(f'ParseTask #{task_id}: нет активных ключевых слов, пропуск')
        return

    total_found = 0

    for source in task.sources.filter(is_active=True):
        try:
            if source.platform == source.PLATFORM_TELEGRAM:
                found = _parse_telegram(source, keywords, keyword_objects)
            elif source.platform == source.PLATFORM_VK:
                found = _parse_vk(source, keywords, keyword_objects)
            elif source.platform == source.PLATFORM_RSS:
                found = _parse_rss(source, keywords, keyword_objects)
            elif source.platform == source.PLATFORM_DZEN:
                found = _parse_dzen(source, keywords, keyword_objects)
            elif hasattr(source, 'PLATFORM_MAX') and source.platform == source.PLATFORM_MAX:
                found = _parse_max(source, keywords, keyword_objects)
            else:
                logger.warning(f'Неизвестная платформа: {source.platform}')
                found = 0

            total_found += found
            logger.info(f'ParseTask #{task_id}, источник "{source.name}": найдено {found}')
        except Exception as exc:
            logger.error(f'Ошибка парсинга источника "{source.name}": {exc}')

    task.last_run_at = timezone.now()
    task.items_found_total += total_found
    task.save(update_fields=['last_run_at', 'items_found_total'])

    logger.info(f'ParseTask #{task_id} завершена: найдено {total_found} новых материалов')
    return total_found


def _match_keywords(text, keywords):
    """Возвращает список ключевых слов, найденных в тексте (регистронезависимо)."""
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]


def _save_item(source, keyword_obj, text, platform_id, original_url=''):
    """Сохраняет найденный материал, если его ещё нет (дедупликация по source + platform_id)."""
    from .models import ParsedItem
    if not platform_id:
        return False
    _, created = ParsedItem.objects.get_or_create(
        source=source,
        platform_id=str(platform_id),
        defaults={
            'keyword': keyword_obj,
            'text': text[:10000],
            'original_url': original_url[:200] if original_url else '',
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
        session_path = str(settings.BASE_DIR / 'media' / 'telethon_session')
        client = TelegramClient(session_path, int(api_id), api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError(
                'Telethon session не авторизована. '
                'Нужно один раз залогиниться (ввести телефон/код) командой '
                '`python3 manage.py telethon_login` внутри web-контейнера.'
            )
        found = 0
        try:
            channel = await client.get_entity(source.source_id)
            # Берём только последние 20 сообщений, чтобы не упереться в лимиты API
            async for message in client.iter_messages(channel, limit=20):
                if not message.text:
                    continue
                matched = _match_keywords(message.text, keywords)
                if matched:
                    kw_obj = keyword_objects[matched[0]]
                    if _save_item(source, kw_obj, message.text, str(message.id)):
                        found += 1
        finally:
            await client.disconnect()
        return found

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()


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
                if _save_item(source, kw_obj, text, f'{owner_id}_{post_id}', url):
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

    for entry in feed.entries[:50]:
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
            if _save_item(source, kw_obj, text, entry_id, link):
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

    for article in articles[:50]:
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


# ─── MAX (заглушка) ──────────────────────────────────────────────────────────
def _parse_max(source, keywords, keyword_objects):
    """Парсинг каналов MAX.

    На стороне MAX нужен источник данных (официальный API/веб-страница/RSS/экспорт).
    Пока оставляем заглушку, чтобы можно было настроить источники/ключевики/задачи
    и не падать при запуске задач.
    """
    logger.warning('MAX парсинг пока не реализован (заглушка). Источник: %s', source.source_id)
    return 0


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
