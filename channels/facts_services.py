"""
Генерация черновиков «интересные факты» через DeepSeek (JSON → текст поста).
"""
from __future__ import annotations

import html
import json
import logging
import re
from typing import Any

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _strip_json_fence(raw: str) -> str:
    s = (raw or '').strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s*```$', '', s)
    return s.strip()


def generate_fact_text_with_ai(topic: str, *, api_key: str) -> tuple[str, str]:
    """Возвращает (plain_text, html_for_telegram)."""
    from django.conf import settings

    from parsing.deepseek_snippet import build_deepseek_client

    topic = (topic or '').strip()
    if not topic:
        raise ValueError('Тема не задана.')

    client = build_deepseek_client(api_key)
    model = getattr(settings, 'DEEPSEEK_MODEL', 'deepseek-chat')
    system = (
        'Ты редактор новостного и познавательного канала. Пиши правдоподобно: '
        'не выдумывай конкретные даты, цифры и «цитаты учёных», если не уверен — '
        'формулируй осторожно («считается», «по данным очевидцев»). '
        'Ответь только JSON-объектом без markdown.'
    )
    user = (
        f'Тема запроса редактора: «{topic}».\n'
        'Сгенерируй один материал для поста: интересный факт или короткая заметка (3–7 предложений), '
        'по делу, без кликбейта в заголовке.\n'
        'JSON с ключами:\n'
        '- "headline" — короткая строка-заголовок (до 120 символов), можно 1 эмодзи.\n'
        '- "body_plain" — основной текст без HTML.\n'
        '- "body_html" — тот же смысл для Telegram HTML: допустимы <b>, <i>, <a href="">; '
        'переносы через \\n или <br>.\n'
        'Язык: русский.'
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        max_tokens=1800,
        temperature=0.75,
    )
    raw = (resp.choices[0].message.content or '').strip()
    data: dict[str, Any] = json.loads(_strip_json_fence(raw))
    if not isinstance(data, dict):
        raise ValueError('Модель вернула не объект JSON.')

    headline = (data.get('headline') or '').strip()
    body_plain = (data.get('body_plain') or '').strip()
    body_html = (data.get('body_html') or '').strip()

    if not body_plain and body_html:
        body_plain = re.sub(r'<[^>]+>', '', body_html)
        body_plain = html.unescape(body_plain).strip()

    if not body_plain:
        raise ValueError('Пустой текст от модели.')

    if headline:
        plain = f'{headline}\n\n{body_plain}'
    else:
        plain = body_plain

    if body_html:
        ht = f'<b>{html.escape(headline)}</b>\n\n{body_html}' if headline else body_html
    elif headline:
        ht = f'<b>{html.escape(headline)}</b>\n\n{html.escape(body_plain).replace(chr(10), "<br>")}'
    else:
        ht = html.escape(body_plain).replace('\n', '<br>')

    return plain.strip(), ht.strip()


def is_facts_due(cfg) -> bool:
    from .models import ChannelInterestingFacts

    if not isinstance(cfg, ChannelInterestingFacts):
        return False
    if not cfg.is_enabled:
        return False
    if not (cfg.topic or '').strip():
        return False
    now = timezone.now()
    if cfg.last_generated_at is None:
        return True
    delta_sec = (now - cfg.last_generated_at).total_seconds()
    return delta_sec >= int(cfg.interval_hours) * 3600


def create_draft_post_for_facts(cfg_id: int, *, force: bool = False) -> tuple[bool, str]:
    """
    Создаёт черновик поста. Возвращает (успех, сообщение).
    """
    from core.models import get_global_api_keys

    from content.models import Post

    from .models import ChannelInterestingFacts

    lock_key = f'interesting_facts_gen:{cfg_id}'
    from django.core.cache import cache

    if not cache.add(lock_key, '1', timeout=120):
        return False, 'Генерация уже выполняется (подождите).'

    try:
        cfg = ChannelInterestingFacts.objects.select_related('channel', 'channel__owner').get(pk=cfg_id)
        channel = cfg.channel

        if not cfg.is_enabled and not force:
            return False, 'Опция выключена.'
        if not force and not is_facts_due(cfg):
            return False, 'Ещё не время по расписанию.'
        if not (cfg.topic or '').strip():
            return False, 'Заполните тему запроса.'
        if not channel.is_active:
            return False, 'Канал неактивен.'

        keys = get_global_api_keys()
        api_key = (keys.get_deepseek_api_key() or '').strip()
        if not api_key:
            return False, 'Не задан ключ DeepSeek (Ключи API).'

        plain, ht = generate_fact_text_with_ai(cfg.topic, api_key=api_key)

        with transaction.atomic():
            cfg2 = ChannelInterestingFacts.objects.select_for_update().get(pk=cfg_id)
            if not cfg2.is_enabled and not force:
                return False, 'Опция выключена.'
            if not force and cfg2.last_generated_at:
                elapsed = (timezone.now() - cfg2.last_generated_at).total_seconds()
                if elapsed < int(cfg2.interval_hours) * 3600:
                    return False, 'Интервал ещё не прошёл (ожидайте следующего окна).'

            post = Post.objects.create(
                author=channel.owner,
                text=plain,
                text_html=ht,
                status=Post.STATUS_DRAFT,
                ord_label='',
            )
            post.channels.add(channel)

            cfg2.last_generated_at = timezone.now()
            cfg2.last_error = ''
            cfg2.save(update_fields=['last_generated_at', 'last_error', 'updated_at'])

        return True, f'Черновик #{post.pk} создан.'
    except Exception as exc:
        logger.exception('interesting facts cfg=%s', cfg_id)
        try:
            ChannelInterestingFacts.objects.filter(pk=cfg_id).update(
                last_error=str(exc)[:2000],
                updated_at=timezone.now(),
            )
        except Exception:
            pass
        return False, str(exc)[:500]
    finally:
        cache.delete(lock_key)


def tick_interesting_facts() -> None:
    from .models import ChannelInterestingFacts

    for cfg in ChannelInterestingFacts.objects.filter(is_enabled=True).select_related('channel'):
        try:
            if not is_facts_due(cfg):
                continue
            ok, msg = create_draft_post_for_facts(cfg.pk, force=False)
            if ok:
                logger.info('interesting facts: cfg=%s %s', cfg.pk, msg)
        except Exception:
            logger.exception('interesting facts tick cfg=%s', cfg.pk)
