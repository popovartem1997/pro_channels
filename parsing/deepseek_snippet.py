"""
Краткий рерайт спарсенного материала в текст поста (DeepSeek API, OpenAI-совместимый клиент).
"""
from __future__ import annotations

import html
import json
import re
from typing import Any

# (id, короткая метка кнопки, подсказка, инструкция для system prompt)
AI_POST_MOOD_SPECS: tuple[tuple[str, str, str, str], ...] = (
    (
        'neutral',
        'Нейтрально',
        'Как новость: спокойно и по делу',
        'Нейтральный редакторский тон: ясно, по факту, без лишних эмоций. Эмодзи не больше одного либо совсем без них.',
    ),
    (
        'cheerful',
        'Весёлое',
        'Позитив, лёгкость, больше жизни',
        'Лёгкий позитивный тон: живая речь, уместный юмор без пошлости, можно 2–4 эмодзи. Заголовок может быть игривым, но факты не искажай.',
    ),
    (
        'serious',
        'Серьёзное',
        'Официально, сухо, без эмодзи',
        'Сдержанный деловой тон: как официальная сводка или пресс-релиз. Без шуток, без эмодзи, без разговорных сокращений.',
    ),
    (
        'warning',
        'Важно',
        'Внимание, срочность, осторожность',
        'Тон важного предупреждения: читатель должен уловить срочность или риск. Без паники и сенсаций, без запугивания. Допустим один знак ⚠️ или без эмодзи.',
    ),
    (
        'inspiring',
        'Вдохновляющее',
        'Гордость, развитие, «мы вместе»',
        'Вдохновляющий тон: развитие, достижения, общие ценности. Умеренный позитив, без пустых лозунгов. Эмодзи по желанию, немного.',
    ),
    (
        'friendly',
        'По-дружески',
        'Тепло и близко к читателю',
        'Тёплый тон на «вы»: как разговор с подписчиками, доброжелательно, без канцелярита. Эмодзи уместны, но не перегружай.',
    ),
    (
        'expert',
        'Экспертно',
        'Цифры, суть, без воды',
        'Экспертный тон: опора на факты и цифры из текста, коротко объясни «что это значит». Без эмодзи или одно нейтральное. Без жаргона ради жаргона.',
    ),
    (
        'dramatic',
        'Динамично',
        'Короткие фразы, акцент на повороте',
        'Динамичный тон: короткие ударные фразы, интрига и масштаб события. Не выдумывай фактов. Эмодзи точечно или не использовать.',
    ),
    (
        'ironic',
        'С иронией',
        'Лёгкая ирония, остроумно',
        'Лёгкая ирония и остроумие без яда, без оскорблений людей и групп, без политических ярлыков. Эмодзи редко. Факты не искажай ради шутки.',
    ),
    (
        'calm',
        'Спокойное',
        'Мягко, без крика в заголовке',
        'Спокойный уверенный тон: без крикливого капса в заголовке, без давления. Информируй мягко и ясно.',
    ),
)

AI_POST_MOODS: list[dict[str, str]] = [
    {'id': a, 'label': b, 'title': c} for a, b, c, _ in AI_POST_MOOD_SPECS
]
_MOOD_INSTRUCTIONS: dict[str, str] = {a: d for a, _, _, d in AI_POST_MOOD_SPECS}
DEFAULT_AI_TONE = 'neutral'


def normalize_ai_tone(raw: str | None) -> str:
    k = (raw or '').strip().lower()
    if k in _MOOD_INSTRUCTIONS:
        return k
    return DEFAULT_AI_TONE


def ai_tone_label(tone: str | None) -> str:
    k = normalize_ai_tone(tone)
    for m in AI_POST_MOODS:
        if m['id'] == k:
            return m['label']
    return 'Нейтрально'


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


# Хвосты, которые модель иногда вставляет вопреки промпту — убираем постобработкой.
_META_LINK_TAIL_RES = (
    re.compile(r'(?:\s|<br\s*/?>)*Подробности\s+в\s+материале\.?\s*$', re.IGNORECASE | re.UNICODE),
    re.compile(r'(?:\s|<br\s*/?>)*Подробнее\s+в\s+материале\.?\s*$', re.IGNORECASE | re.UNICODE),
    re.compile(r'(?:\s|<br\s*/?>)*Читайте\s+в\s+материале\.?\s*$', re.IGNORECASE | re.UNICODE),
    re.compile(r'(?:\s|<br\s*/?>)*Полный\s+текст\s*[—\-]?\s*в\s+материале\.?\s*$', re.IGNORECASE | re.UNICODE),
    re.compile(r'(?:\s|<br\s*/?>)*В\s+материале\.?\s*$', re.IGNORECASE | re.UNICODE),
)


def _strip_meta_material_tail(s: str) -> str:
    if not (s or '').strip():
        return s
    t = s
    for _ in range(4):
        prev = t
        for rx in _META_LINK_TAIL_RES:
            t = rx.sub('', t)
        t = t.rstrip()
        if t == prev:
            break
    return t


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


def _build_rewrite_system_prompt(
    *,
    tone_rule: str,
    with_headline: bool,
    embed_source_link: bool,
) -> str:
    base = (
        'Ты редактор постов для соцсетей. Пиши по-русски. '
        'Не упоминай «источник», парсинг и технические детали. '
        'Не делай строки «источник:» и мета-отсылки к СМИ.'
    )

    if with_headline and embed_source_link:
        structure = (
            'Структура поста: (1) один громкий цепляющий ЗАГОЛОВОК — короткая строка, можно частично КАПСОМ и с 1–2 уместными эмодзи; '
            '(2) ТЕЛО — 2–4 коротких предложения простым языком, без воды. '
            'Если передан URL оригинала: в body_html встрой ссылку РОВНО ОДИН раз в ПЕРВОМ или ВТОРОМ предложении тела, '
            'внутри факта: оберни в <a href="URL">…</a> обычную смысловую фразу из этого же предложения (3–7 слов), '
            'которая описывает событие или действие. Примеры хорошего якоря: «в округе появились новые учебные заведения», '
            '«за пять лет ввели в строй», «педагогам выделили гранты» — то есть не отсылка к «статье», а касательно сути новости. '
            'ЗАПРЕЩЕНО: любые формулировки про «материал», «статью», «публикацию», «подробности» как отдельное предложение или хвост текста — '
            'в частности нельзя писать «Подробности в материале», «в материале», «читайте в материале», «полный текст», '
            '«подробнее в статье», отдельное финальное предложение только ради ссылки. '
            'Также запрещены якоря «Подробнее», «Читать далее», «Ещё», «Тут», «Ссылка». '
            'Для body_plain (VK/MAX): то же тело без тегов; полный URL один раз в конце последнего предложения через пробел, без фразы «подробности». '
            'Если URL нет — тело без ссылок. '
            'Ответ строго JSON без markdown-обёртки: '
            '{"headline": "заголовок", "body_plain": "только тело без заголовка", "body_html": "только тело в HTML с <i> по желанию и одной <a> если есть URL"}.'
        )
    elif with_headline and not embed_source_link:
        structure = (
            'Структура поста: (1) один громкий цепляющий ЗАГОЛОВОК — короткая строка, можно частично КАПСОМ и с 1–2 уместными эмодзи; '
            '(2) ТЕЛО — 2–4 коротких предложения простым языком, без воды. '
            'Не вставляй в текст никаких URL и тегов <a>, даже если URL передан — он только для твоего контекста, в пост не включай. '
            'Ответ строго JSON без markdown-обёртки: '
            '{"headline": "заголовок", "body_plain": "только тело без заголовка", "body_html": "только тело в HTML, допускается <i>, без ссылок"}.'
        )
    elif not with_headline and embed_source_link:
        structure = (
            'Пост — единый связный текст БЕЗ отдельной строки-заголовка: 2–5 коротких предложений простым языком. '
            'Если передан URL оригинала: в body_html встрой ссылку РОВНО ОДИН раз в первом или втором предложении, '
            'внутри факта (<a href="URL">смысловая фраза 3–7 слов</a>), без отсылок к «статье» и «материалу». '
            'Для body_plain: то же без тегов; URL один раз в конце последнего предложения через пробел. Если URL нет — без ссылок. '
            'Ответ строго JSON: {"headline": "", "body_plain": "весь текст поста", "body_html": "весь текст в HTML"}. '
            'Поле headline всегда пустая строка. '
            'Допустим устаревший формат {"plain": "...", "html": "..."} — тогда это весь пост целиком, без отдельного заголовка.'
        )
    else:
        structure = (
            'Пост — единый связный текст БЕЗ отдельного заголовка: 2–5 коротких предложений простым языком. '
            'Не вставляй URL и теги <a>, даже если URL передан контекстом. '
            'Ответ строго JSON: {"headline": "", "body_plain": "весь текст", "body_html": "весь текст в HTML без ссылок"}. '
            'Поле headline всегда пустая строка. '
            'Допустим {"plain": "...", "html": "..."} — весь пост целиком.'
        )

    return f'{base}\n{structure}\n\nГЛАВНОЕ ПРАВИЛО ТОНА И СТИЛЯ (обязательно соблюдай): {tone_rule}'


def _build_rewrite_user_message(
    *,
    safe_text: str,
    safe_url: str,
    tone_key: str,
    with_headline: bool,
    embed_source_link: bool,
) -> str:
    parts = [f'Исходный текст:\n{safe_text}\n']
    if embed_source_link:
        parts.append(f'URL оригинала (если пусто — ссылку не вставляй): {safe_url}\n')
        parts.append(
            'Если URL есть: в HTML обязательно спрячь его внутри фактического фрагмента первого/второго предложения '
            '(как в примере со словами «в округе появились»), без отдельной строки про материал или подробности.\n'
        )
    else:
        parts.append(
            f'Контекст: URL оригинала (не публикуй в тексте): {safe_url}\n'
            'В финальный пост ссылки и URL не включай.\n'
        )
    scope = 'заголовок и тело' if with_headline else 'весь пост'
    parts.append(f'Выбранное настроение поста: «{tone_key}» — {scope} должны ему соответствовать.')
    return ''.join(parts)


def rewrite_for_feed_post(
    *,
    original_text: str,
    source_url: str,
    api_key: str,
    model_name: str,
    tone: str | None = None,
    tone_rule: str | None = None,
    with_headline: bool = True,
    embed_source_link: bool = False,
) -> tuple[str, str]:
    """
    Возвращает (plain_text, html_for_telegram).
    Формат ответа модели — JSON с headline + телом; допускается старый формат plain/html.
    tone — ключ настроения; tone_rule — явная инструкция тона (кастомные кнопки в ленте).
    Если tone_rule передан, он главный; tone_key для подписи — как в запросе (id кнопки).
    """
    tr = (tone_rule or '').strip()
    raw = (tone or '').strip().lower()
    if tr:
        tone_key = raw or DEFAULT_AI_TONE
        rule = tr
    else:
        tone_key = normalize_ai_tone(tone)
        rule = _MOOD_INSTRUCTIONS.get(tone_key, _MOOD_INSTRUCTIONS[DEFAULT_AI_TONE])

    safe_text = html.escape((original_text or '').strip()[:8000], quote=False)
    url = (source_url or '').strip()
    safe_url = html.escape(url, quote=True) if url else ''

    system = _build_rewrite_system_prompt(
        tone_rule=rule,
        with_headline=with_headline,
        embed_source_link=embed_source_link,
    )

    user_msg = _build_rewrite_user_message(
        safe_text=safe_text,
        safe_url=safe_url,
        tone_key=tone_key,
        with_headline=with_headline,
        embed_source_link=embed_source_link,
    )

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
        t = _strip_meta_material_tail(t)
        cleaned = _strip_meta_material_tail(cleaned)
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
            return _strip_meta_material_tail(plain), _strip_meta_material_tail(ht)

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
    return _strip_meta_material_tail(plain), _strip_meta_material_tail(ht)
