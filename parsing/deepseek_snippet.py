"""
Краткий рерайт спарсенного материала в текст поста (DeepSeek API, OpenAI-совместимый клиент).
"""
from __future__ import annotations

import html
import json
import re
from typing import Any


def build_deepseek_client(api_key: str):
    from django.conf import settings
    from openai import OpenAI

    base = (getattr(settings, 'DEEPSEEK_API_BASE', '') or 'https://api.deepseek.com').rstrip('/')
    return OpenAI(api_key=api_key, base_url=base)


def _strip_json_fence(raw: str) -> str:
    s = (raw or '').strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s*```$', '', s)
    return s.strip()


def _compose_headline_post(*, headline: str, body_plain: str, body_html: str) -> tuple[str, str]:
    """Собирает итоговый plain и HTML из частей."""
    hl = (headline or '').strip()
    bp = (body_plain or '').strip()
    bh = (body_html or '').strip()

    if hl and bp:
        plain = f'{hl}\n\n{bp}'
    elif hl:
        plain = hl
    else:
        plain = bp

    if bh:
        ht = f'<b>{html.escape(hl)}</b>\n\n{bh}' if hl else bh
    elif hl and bp:
        ht = f'<b>{html.escape(hl)}</b>\n\n{html.escape(bp).replace(chr(10), "<br>")}'
    elif hl:
        ht = f'<b>{html.escape(hl)}</b>'
    elif bp:
        ht = html.escape(bp).replace('\n', '<br>')
    else:
        ht = ''

    return plain.strip(), ht.strip()


def rewrite_for_feed_post(
    *,
    original_text: str,
    source_url: str,
    api_key: str,
    model_name: str,
) -> tuple[str, str]:
    """
    Возвращает (plain_text, html_for_telegram).
    Формат ответа модели — JSON с headline + телом; допускается старый формат plain/html.
    """
    safe_text = html.escape((original_text or '').strip()[:8000], quote=False)
    url = (source_url or '').strip()
    safe_url = html.escape(url, quote=True) if url else ''

    system = (
        'Ты редактор постов для соцсетей. Пиши по-русски. '
        'Структура поста: (1) один громкий цепляющий ЗАГОЛОВОК — короткая строка, можно частично КАПСОМ и с 1–2 уместными эмодзи; '
        '(2) ТЕЛО — 2–4 коротких предложения простым языком, без воды. '
        'Не упоминай «источник», парсинг и технические детали. '
        'Если передан URL оригинала: в теле для HTML встрой его РОВНО ОДИН раз как '
        '<a href="URL">органичная фраза внутри предложения</a> — якорь должен быть обычным смысловым фрагментом текста '
        '(например название события, «в материале издания», «на странице портала»). '
        'Категорически запрещены якорные тексты вроде «Подробнее», «Читать далее», «Ещё», «Тут», «Ссылка» отдельной кнопкой или строкой. '
        'Не делай отдельной строки «источник:» или похожего. '
        'Для plain-текста (VK/MAX) в конце тела один раз допиши полный URL через пробел, если URL был дан; без дублирования в заголовке. '
        'Если URL нет — тело без ссылок. '
        'Ответ строго JSON без markdown-обёртки: '
        '{"headline": "заголовок", "body_plain": "только тело без заголовка", "body_html": "только тело в HTML с <i> по желанию и одной <a> если есть URL"}. '
        'Допустим устаревший формат {"plain": "...", "html": "..."} — тогда интерпретируй как единый блок без отдельного заголовка.'
    )

    user_msg = f'Исходный текст:\n{safe_text}\n\nURL оригинала (если пусто — ссылку не вставляй): {safe_url}'

    client = build_deepseek_client(api_key)
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
        t = re.sub(r'<[^>]+>', '', cleaned).strip() or cleaned.strip()
        return t, cleaned

    hl = (data.get('headline') or '').strip()
    body_plain = (data.get('body_plain') or '').strip()
    body_html = (data.get('body_html') or '').strip()

    if hl or body_plain or body_html:
        plain, ht = _compose_headline_post(headline=hl, body_plain=body_plain, body_html=body_html)
        if not plain and ht:
            plain = re.sub(r'<[^>]+>', ' ', ht)
            plain = re.sub(r'\s+', ' ', plain).strip()
        if plain or ht:
            return plain, ht

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
