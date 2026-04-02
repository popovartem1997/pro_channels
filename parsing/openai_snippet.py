"""
Краткий рерайт спарсенного материала в текст поста (OpenAI).
"""
from __future__ import annotations

import html
import json
import re
from typing import Any


def _strip_json_fence(raw: str) -> str:
    s = (raw or '').strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s*```$', '', s)
    return s.strip()


def rewrite_for_feed_post(
    *,
    original_text: str,
    source_url: str,
    api_key: str,
    model_name: str,
) -> tuple[str, str]:
    """
    Возвращает (plain_text, html_for_telegram).
    """
    from openai import OpenAI

    safe_text = html.escape((original_text or '').strip()[:8000], quote=False)
    url = (source_url or '').strip()
    safe_url = html.escape(url, quote=True) if url else ''

    system = (
        'Ты редактор постов для соцсетей. Пиши по-русски. '
        'Сделай из исходного текста короткий пост (2–5 коротких предложений или строк), '
        'простым языком, без воды и канцелярита. '
        'Добавь 1–3 уместных эмодзи в текст (не перегружай). '
        'Не упоминай «источник», названия СМИ и парсинг. '
        'Если есть URL оригинала — встрой его ОДИН раз как HTML-ссылку '
        '<a href="URL">одно короткое слово или словосочетание</a> (например «подробности», «ещё», «читать»), '
        'естественно внутри фразы, без отдельной строки «источник:». '
        'Если URL нет — просто короткий пост без ссылки. '
        'Ответ строго в JSON без markdown-обёртки: '
        '{"plain": "текст без HTML для простых платформ", '
        '"html": "тот же смысл с <b>/<i> по необходимости и одной ссылкой <a href=...> если есть URL"}. '
        'В plain используй тот же текст что видит пользователь, но без тегов (эмодзи можно).'
    )

    user_msg = f'Исходный текст:\n{safe_text}\n\nURL оригинала (если пусто — ссылку не вставляй): {safe_url}'

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg},
        ],
        max_tokens=900,
        temperature=0.7,
    )
    raw = (response.choices[0].message.content or '').strip()
    cleaned = _strip_json_fence(raw)
    try:
        data: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError:
        # fallback: весь ответ как plain
        t = re.sub(r'<[^>]+>', '', cleaned).strip() or cleaned.strip()
        return t, cleaned

    plain = (data.get('plain') or '').strip()
    ht = (data.get('html') or '').strip()
    if not plain and ht:
        plain = re.sub(r'<[^>]+>', ' ', ht)
        plain = re.sub(r'\s+', ' ', plain).strip()
    if not ht and plain:
        ht = html.escape(plain).replace('\n', '<br>')
    if not plain:
        plain = re.sub(r'<[^>]+>', ' ', ht)
        plain = re.sub(r'\s+', ' ', plain).strip()
    return plain, ht
