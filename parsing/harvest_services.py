"""
Ключевики с примера Telegram-канала: нормализация @канала, DeepSeek, создание ParseKeyword.
"""
from __future__ import annotations

import json
import logging
import re
logger = logging.getLogger(__name__)

MAX_COMBINED_POSTS_CHARS = 14_000
MAX_KEYWORDS_FROM_AI = 40
KEYWORD_MAX_LEN = 255


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


def apply_harvest_keywords(job, keywords_to_add: list[str]) -> int:
    """
    Создаёт ParseKeyword по списку (уже без отклонённых пользователем).
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

    # Источник Telegram = канал, откуда брались посты для AI (один ParseSource на каждый целевой канал).
    for ch in channels:
        ensure_example_telegram_parse_source(
            owner=owner,
            channel=ch,
            channel_group=group,
            example_channel_raw=job.example_channel,
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
    for phrase in keywords_to_add:
        kw = (phrase or '').strip()
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
