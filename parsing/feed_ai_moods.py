"""
Кастомные кнопки «интонации» для AI в ленте (парсинг): хранение у владельца workspace.
"""
from __future__ import annotations

import re
from typing import Any

from django.contrib.auth import get_user_model

from parsing.deepseek_snippet import AI_POST_MOOD_SPECS, AI_POST_MOODS, DEFAULT_AI_TONE

User = get_user_model()

_MOOD_ID_RE = re.compile(r'^[a-z0-9_]{1,40}$')
_MAX_MOODS = 24
_MAX_LABEL = 80
_MAX_TITLE = 200
_MAX_INSTRUCTION = 2000


def workspace_owner_for_parsed_item(item) -> User | None:
    """Владелец канала/источника для материала парсинга (настройки AI)."""
    kw = getattr(item, 'keyword', None)
    if kw is not None:
        ch = getattr(kw, 'channel', None)
        if ch is not None:
            return getattr(ch, 'owner', None)
        return getattr(kw, 'owner', None)
    src = getattr(item, 'source', None)
    if src is not None:
        return getattr(src, 'owner', None)
    return None


def built_in_moods_list() -> list[dict[str, str]]:
    return [{'id': a, 'label': b, 'title': c, 'instruction': d} for a, b, c, d in AI_POST_MOOD_SPECS]


def moods_list_for_owner(owner: User | None) -> list[dict[str, str]]:
    """Список настроений для шаблона (id, label, title); instruction — для промпта."""
    raw = getattr(owner, 'feed_ai_moods', None) if owner else None
    if not raw or not isinstance(raw, list):
        return built_in_moods_list()
    out: list[dict[str, str]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        i = str(row.get('id') or '').strip().lower()
        label = str(row.get('label') or '').strip()[:_MAX_LABEL]
        title = str(row.get('title') or '').strip()[:_MAX_TITLE]
        instr = str(row.get('instruction') or '').strip()[:_MAX_INSTRUCTION]
        if not i or not label or not instr:
            continue
        if not _MOOD_ID_RE.match(i):
            continue
        out.append({'id': i, 'label': label, 'title': title or label, 'instruction': instr})
    if len(out) < 1:
        return built_in_moods_list()
    return out


def mood_instructions_map(owner: User | None) -> dict[str, str]:
    return {m['id']: m['instruction'] for m in moods_list_for_owner(owner)}


def moods_for_template(owner: User | None) -> list[dict[str, str]]:
    """Только поля для кнопок (без тяжёлого instruction в title)."""
    return [{'id': m['id'], 'label': m['label'], 'title': m['title']} for m in moods_list_for_owner(owner)]


def normalize_ai_tone_for_owner(raw: str | None, owner: User | None) -> str:
    k = (raw or '').strip().lower()
    mp = mood_instructions_map(owner)
    if k in mp:
        return k
    if DEFAULT_AI_TONE in mp:
        return DEFAULT_AI_TONE
    if mp:
        return next(iter(mp))
    return DEFAULT_AI_TONE


def ai_tone_label_for_owner(tone: str | None, owner: User | None) -> str:
    k = normalize_ai_tone_for_owner(tone, owner)
    for m in moods_for_template(owner):
        if m['id'] == k:
            return m['label']
    for m in AI_POST_MOODS:
        if m['id'] == k:
            return m['label']
    return 'Нейтрально'


def validate_moods_payload(data: Any) -> tuple[list[dict[str, str]] | None, str | None]:
    """
    Проверка тела сохранения. Возвращает (нормализованный список, ошибка).
    """
    if not isinstance(data, list):
        return None, 'Ожидался список настроений'
    if len(data) < 1:
        return None, 'Нужна хотя бы одна интонация'
    if len(data) > _MAX_MOODS:
        return None, f'Не больше {_MAX_MOODS} вариантов'
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in data:
        if not isinstance(row, dict):
            return None, 'Некорректный элемент списка'
        i = str(row.get('id') or '').strip().lower()
        label = str(row.get('label') or '').strip()[:_MAX_LABEL]
        title = str(row.get('title') or '').strip()[:_MAX_TITLE]
        instr = str(row.get('instruction') or '').strip()[:_MAX_INSTRUCTION]
        if not i or not label or not instr:
            return None, 'У каждой интонации нужны id, краткая метка и инструкция для AI'
        if not _MOOD_ID_RE.match(i):
            return None, f'Недопустимый id «{i}»: только латиница, цифры и подчёркивание, до 40 символов'
        if i in seen:
            return None, f'Повтор id «{i}»'
        seen.add(i)
        out.append({'id': i, 'label': label, 'title': title or label, 'instruction': instr})
    return out, None


def can_manage_feed_ai_moods(actor, owner: User | None) -> bool:
    if not owner or not actor or not getattr(actor, 'is_authenticated', False):
        return False
    if getattr(actor, 'is_staff', False) or getattr(actor, 'is_superuser', False):
        return True
    if actor.pk == owner.pk:
        return True
    role = getattr(actor, 'role', '') or ''
    if role not in ('manager', 'assistant_admin'):
        return False
    from managers.models import TeamMember

    return TeamMember.objects.filter(member=actor, owner=owner, is_active=True).exists()
