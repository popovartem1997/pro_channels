"""
Ключевики с примера Telegram-канала: нормализация @канала, DeepSeek, создание ParseKeyword.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

MAX_COMBINED_POSTS_CHARS = 14_000
MAX_DIGEST_CHARS_FOR_AI = 48_000
MAX_KEYWORDS_FROM_AI = 45
KEYWORD_MAX_LEN = 255
MAX_HARVEST_EXAMPLE_CHANNELS = 20


def parse_example_channels_from_post(text: str) -> list[str]:
    """Разбор многострочного списка каналов (@, ссылки t.me). До MAX_HARVEST_EXAMPLE_CHANNELS штук."""
    out: list[str] = []
    for part in re.split(r'[\n\r,;]+', text or ''):
        part = part.strip()
        if not part:
            continue
        r = normalize_telegram_channel_ref(part)
        if r:
            out.append(r)
    seen: set[str] = set()
    uniq: list[str] = []
    for r in out:
        k = r.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq[:MAX_HARVEST_EXAMPLE_CHANNELS]


def suggestion_row_phrase(entry) -> str:
    """Текст ключевика из элемента suggested_keywords (строка или dict)."""
    if isinstance(entry, dict):
        return (entry.get('phrase') or entry.get('keyword') or '').strip()
    return str(entry or '').strip()


def normalize_ranked_keyword_rows(raw_list: list) -> list[dict]:
    """Приводит ответ AI к списку dict: phrase, repeat_score, comment."""
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(raw_list, list):
        return out
    for x in raw_list:
        if isinstance(x, str):
            phrase = x.strip()
            repeat_score = None
            comment = ''
        elif isinstance(x, dict):
            phrase = (x.get('phrase') or x.get('keyword') or '').strip()
            repeat_score = x.get('repeat_score', x.get('score'))
            comment = (x.get('comment') or x.get('note') or '').strip()
        else:
            continue
        if not phrase:
            continue
        phrase = phrase[:KEYWORD_MAX_LEN]
        low = phrase.lower()
        if low in seen:
            continue
        seen.add(low)
        rs = None
        if repeat_score is not None:
            try:
                rs = max(1, min(10, int(repeat_score)))
            except (TypeError, ValueError):
                rs = None
        out.append(
            {
                'phrase': phrase,
                'repeat_score': rs,
                'comment': comment[:500],
            }
        )
        if len(out) >= MAX_KEYWORDS_FROM_AI:
            break
    return out


def ensure_example_telegram_parse_source(
    *,
    owner,
    channel,
    channel_group,
    example_channel_raw: str,
):
    """
    Создаёт или находит ParseSource (Telegram) с source_id = канал-пример,
    привязанный к целевому каналу публикации и группе.
    """
    from .models import ParseSource

    ref = normalize_telegram_channel_ref(example_channel_raw)
    if not ref:
        return None
    qs = ParseSource.objects.filter(
        owner=owner,
        channel=channel,
        platform=ParseSource.PLATFORM_TELEGRAM,
        source_id=ref,
    )
    src = qs.first()
    if src:
        updates = []
        if not src.is_active:
            src.is_active = True
            updates.append('is_active')
        if channel_group and src.channel_group_id != channel_group.id:
            src.channel_group = channel_group
            updates.append('channel_group')
        if updates:
            src.save(update_fields=updates)
        return src
    return ParseSource.objects.create(
        owner=owner,
        channel=channel,
        channel_group=channel_group,
        platform=ParseSource.PLATFORM_TELEGRAM,
        source_id=ref,
        name=f'{ref} (пример для ключевиков)'[:255],
        is_active=True,
    )


def normalize_telegram_channel_ref(raw: str) -> str:
    s = (raw or '').strip()
    if not s:
        return ''
    low = s.lower()
    if 't.me/' in low:
        part = s.split('t.me/', 1)[1].split('?')[0].strip('/')
        username = part.split('/')[0].strip()
        if username:
            return '@' + username.lstrip('@')
    if s.startswith('@'):
        return s
    return '@' + s.lstrip('@')


def harvest_example_channel_refs(job) -> list[str]:
    """Список нормализованных @каналов из example_channels или legacy example_channel."""
    refs: list[str] = []
    raw = getattr(job, 'example_channels', None)
    if isinstance(raw, list):
        for x in raw:
            r = normalize_telegram_channel_ref(str(x))
            if r:
                refs.append(r)
    if not refs:
        r = normalize_telegram_channel_ref(getattr(job, 'example_channel', '') or '')
        if r:
            refs.append(r)
    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        k = r.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out[:MAX_HARVEST_EXAMPLE_CHANNELS]


def _strip_json_fence(raw: str) -> str:
    s = (raw or '').strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s*```$', '', s)
    return s.strip()


def extract_keywords_with_deepseek(*, posts_digest_text: str, region_prompt: str, api_key: str) -> list[str]:
    """Возвращает список строк-ключевиков (рус.) из ответа DeepSeek."""
    from django.conf import settings

    from parsing.deepseek_snippet import build_deepseek_client

    region = (region_prompt or '').strip()[:4000]
    body = (posts_digest_text or '').strip()
    if len(body) > MAX_COMBINED_POSTS_CHARS:
        body = body[:MAX_COMBINED_POSTS_CHARS] + '\n…'

    user = (
        'Ниже — фрагменты последних постов из публичного Telegram-канала (пример того, что нравится автору).\n'
        'Ниже также — описание географии и контекста пользователя (свой район, Московская область и т.д.).\n\n'
        f'--- Контекст района / задачи ---\n{region}\n\n'
        f'--- Посты (пример) ---\n{body}\n\n'
        'Сформируй список ключевых слов и коротких фраз на русском для мониторинга новостей/контента '
        'в соцсетях и мессенджерах, чтобы находить материалы в духе примера, но релевантные указанному району '
        'и контексту (география, местные названия, темы). Не копируй дословно заголовки; обобщай темы.\n'
        f'Верни один JSON-объект: {{"keywords": ["фраза1", "фраза2", ...]}}.\n'
        f'Не больше {MAX_KEYWORDS_FROM_AI} элементов; каждая строка не длиннее 120 символов; без дубликатов; '
        'без пояснений вне JSON.'
    )

    client = build_deepseek_client(api_key)
    model = getattr(settings, 'DEEPSEEK_MODEL', 'deepseek-chat')
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                'role': 'system',
                'content': 'Отвечай только валидным JSON-объектом с ключом keywords (массив строк), без markdown.',
            },
            {'role': 'user', 'content': user},
        ],
        max_tokens=2000,
        temperature=0.45,
    )
    raw = (resp.choices[0].message.content or '').strip()
    data = json.loads(_strip_json_fence(raw))
    if not isinstance(data, dict):
        return []
    arr = data.get('keywords') or data.get('keyword') or []
    if not isinstance(arr, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for x in arr:
        s = str(x).strip()
        if not s:
            continue
        s = s[:KEYWORD_MAX_LEN]
        low = s.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(s)
        if len(out) >= MAX_KEYWORDS_FROM_AI:
            break
    return out


def extract_ranked_keywords_with_deepseek(*, posts_digest_text: str, region_prompt: str, api_key: str) -> list[dict]:
    """
    Ключевики с оценкой «повторяемости» (1–10) и коротким комментарием по запросу района.
    Возвращает список dict: phrase, repeat_score, comment.
    """
    from django.conf import settings

    from parsing.deepseek_snippet import build_deepseek_client

    region = (region_prompt or '').strip()[:4000]
    body = (posts_digest_text or '').strip()
    if len(body) > MAX_DIGEST_CHARS_FOR_AI:
        body = body[:MAX_DIGEST_CHARS_FOR_AI] + '\n…'

    user = (
        'Ниже — фрагменты последних постов из одного или нескольких публичных Telegram-каналов '
        '(примеры того, какой контент интересен). Также — описание географии и контекста пользователя '
        '(район, регион, тематика).\n\n'
        f'--- Контекст района / задачи ---\n{region}\n\n'
        f'--- Посты (примеры каналов) ---\n{body}\n\n'
        'Проанализируй тексты и выдели ключевые слова и короткие фразы на русском для мониторинга '
        'новостей/контента в соцсетях и мессенджерах. Учитывай, что посты могут быть с разных каналов: '
        'оцени, насколько тема **типична и повторяется** в этом наборе (не дословное копирование заголовков, '
        'а обобщённые формулировки). Адаптируй формулировки под указанный район и контекст.\n'
        'Верни один JSON-объект вида:\n'
        '{"keywords": [\n'
        '  {"phrase": "строка", "repeat_score": 8, "comment": "кратко почему и для района"},\n'
        '  ...\n'
        ']}\n'
        f'repeat_score — целое от 1 до 10 (насколько тема заметна/повторяется в выборке); '
        f'не больше {MAX_KEYWORDS_FROM_AI} элементов; phrase не длиннее 120 символов; '
        'без дубликатов phrase; без markdown вне JSON.'
    )

    client = build_deepseek_client(api_key)
    model = getattr(settings, 'DEEPSEEK_MODEL', 'deepseek-chat')
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                'role': 'system',
                'content': (
                    'Отвечай только валидным JSON-объектом с ключом keywords — массив объектов '
                    'с полями phrase (строка), repeat_score (1–10), comment (строка). Без markdown.'
                ),
            },
            {'role': 'user', 'content': user},
        ],
        max_tokens=3500,
        temperature=0.4,
    )
    raw = (resp.choices[0].message.content or '').strip()
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    arr = data.get('keywords') or data.get('ranked_keywords') or []
    if not isinstance(arr, list):
        return []
    return normalize_ranked_keyword_rows(arr)


def normalize_suggestion_list_for_ui(raw) -> list[dict]:
    """Единый формат строк для шаблонов: phrase, repeat_score, comment."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for x in raw:
        if isinstance(x, dict):
            phrase = suggestion_row_phrase(x)
            rs = x.get('repeat_score', x.get('score'))
            try:
                rs = max(1, min(10, int(rs))) if rs is not None else None
            except (TypeError, ValueError):
                rs = None
            comment = (x.get('comment') or x.get('note') or '').strip()[:500]
        else:
            phrase = str(x or '').strip()
            rs = None
            comment = ''
        if not phrase:
            continue
        out.append({'phrase': phrase[:KEYWORD_MAX_LEN], 'repeat_score': rs, 'comment': comment})
    return out


def apply_harvest_keywords(job, keywords_to_add: list) -> int:
    """
    Создаёт ParseKeyword по списку (уже без отклонённых пользователем).
    Элементы — строки или dict с полем phrase (как в suggested_keywords).
    Возвращает число созданных записей.
    """
    from channels.models import Channel

    from .models import ParseKeyword, ParseSource
    from .schedule_sync import sync_auto_parse_tasks_for_channel

    group = job.channel_group
    owner = group.owner

    if job.target_mode == job.TARGET_GROUP_ONE:
        ch = job.target_channel
        if ch is None or ch.channel_group_id != group.id:
            raise ValueError('Некорректный канал для задачи.')
        channels = [ch]
    else:
        channels = list(
            Channel.objects.filter(channel_group=group, is_active=True)
            .exclude(platform__in=(Channel.PLATFORM_MAX, Channel.PLATFORM_INSTAGRAM))
            .order_by('platform', 'name')
        )

    if not channels:
        return 0

    example_refs = [r for r in harvest_example_channel_refs(job) if r]
    if not example_refs:
        r = normalize_telegram_channel_ref(job.example_channel or '')
        if r:
            example_refs = [r]

    # Источник Telegram = каждый канал-пример (ParseSource на целевой канал × пример).
    for ch in channels:
        for ref in example_refs:
            if ref:
                ensure_example_telegram_parse_source(
                    owner=owner,
                    channel=ch,
                    channel_group=group,
                    example_channel_raw=ref,
                )
    for ch in channels:
        try:
            sync_auto_parse_tasks_for_channel(ch)
        except Exception:
            logger.exception('harvest: sync after example source ch=%s', ch.pk)

    sources_qs = ParseSource.objects.filter(owner=owner, channel_group=group, is_active=True)
    source_pks = list(sources_qs.values_list('pk', flat=True))

    created = 0
    seen: set[str] = set()
    for item in keywords_to_add:
        kw = suggestion_row_phrase(item)
        if not kw:
            continue
        kw = kw[:KEYWORD_MAX_LEN]
        low = kw.lower()
        if low in seen:
            continue
        seen.add(low)
        for ch in channels:
            if ParseKeyword.objects.filter(channel=ch, keyword__iexact=kw).exists():
                continue
            pk_w = ParseKeyword.objects.create(
                owner=owner,
                channel=ch,
                channel_group=group,
                keyword=kw,
            )
            if source_pks:
                pk_w.sources.set(source_pks)
            try:
                sync_auto_parse_tasks_for_channel(ch)
            except Exception:
                logger.exception('harvest: auto parse sync channel=%s', ch.pk)
            created += 1
    return created
