"""
Утренний дайджест для каналов.

- Погода: Open-Meteo (без ключа).
- Солнце: sunrise-sunset.org (без ключа).
- Праздники: библиотека holidays.
- Цитата / слово / гороскоп: DeepSeek (текст), если включено и задан ключ в «Ключи API».
  Гороскоп: при «Общий» — по всем знакам зодиака; при выборе знака — только для него.
- Картинка: picsum.photos по seed; при недоступности (часто на хостинге в РФ) — запасной JPEG через Pillow.
  Если задан HTTP/SOCKS-прокси в «Ключи API», скачивание picsum идёт через него.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from collections import Counter
from html import escape as html_escape
from typing import Any
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)

WMO_DESC = {
    0: 'ясно',
    1: 'преимущественно ясно',
    2: 'переменная облачность',
    3: 'пасмурно',
    45: 'туман',
    48: 'туман с инеем',
    51: 'морось',
    53: 'морось',
    55: 'морось',
    61: 'небольшой дождь',
    63: 'дождь',
    65: 'ливень',
    71: 'снег',
    73: 'снег',
    75: 'снег',
    77: 'снежные зерна',
    80: 'ливень',
    81: 'ливень',
    82: 'сильный ливень',
    85: 'снегопады',
    86: 'снегопады',
    95: 'гроза',
    96: 'гроза с градом',
    99: 'гроза с градом',
}

ZODIAC_LABELS = {
    'general': 'общий',
    'aries': 'Овен',
    'taurus': 'Телец',
    'gemini': 'Близнецы',
    'cancer': 'Рак',
    'leo': 'Лев',
    'virgo': 'Дева',
    'libra': 'Весы',
    'scorpio': 'Скорпион',
    'sagittarius': 'Стрелец',
    'capricorn': 'Козерог',
    'aquarius': 'Водолей',
    'pisces': 'Рыбы',
}

# Порядок вывода в дайджесте (общий гороскоп по всем знакам)
ZODIAC_ORDER: list[tuple[str, str, str]] = [
    ('aries', '♈', 'Овен'),
    ('taurus', '♉', 'Телец'),
    ('gemini', '♊', 'Близнецы'),
    ('cancer', '♋', 'Рак'),
    ('leo', '♌', 'Лев'),
    ('virgo', '♍', 'Дева'),
    ('libra', '♎', 'Весы'),
    ('scorpio', '♏', 'Скорпион'),
    ('sagittarius', '♐', 'Стрелец'),
    ('capricorn', '♑', 'Козерог'),
    ('aquarius', '♒', 'Водолей'),
    ('pisces', '♓', 'Рыбы'),
]

PERIOD_LABELS = [
    ('mor', 'Утром', 'утром'),
    ('day', 'Днём', 'днём'),
    ('eve', 'Вечером', 'вечером'),
    ('night', 'Ночью', 'ночью'),
]


def _wind_dir_ru(deg: float | None) -> str:
    if deg is None:
        return 'переменный'
    names = [
        'северный',
        'северо-восточный',
        'восточный',
        'юго-восточный',
        'южный',
        'юго-западный',
        'западный',
        'северо-западный',
    ]
    idx = int((float(deg) + 22.5) // 45) % 8
    return names[idx]


def _mmhg(hpa: float | None) -> int:
    if hpa is None:
        return 0
    return int(round(float(hpa) * 0.750061683))


def _ms(kmh: float | None) -> float:
    if kmh is None:
        return 0.0
    return round(float(kmh) / 3.6, 1)


def _wmo_word(code: float | int | None) -> str:
    if code is None:
        return 'без осадков'
    try:
        c = int(code)
    except (TypeError, ValueError):
        return 'облачно'
    return WMO_DESC.get(c, 'облачно')


def _bucket_key(local_hour: int) -> str:
    if 6 <= local_hour <= 11:
        return 'mor'
    if 12 <= local_hour <= 17:
        return 'day'
    if 18 <= local_hour <= 22:
        return 'eve'
    return 'night'


def fetch_open_meteo_day(lat: float, lon: float, tz_name: str, day: dt.date) -> dict[str, Any] | None:
    try:
        r = requests.get(
            'https://api.open-meteo.com/v1/forecast',
            params={
                'latitude': lat,
                'longitude': lon,
                'hourly': (
                    'temperature_2m,relative_humidity_2m,pressure_msl,'
                    'wind_speed_10m,wind_direction_10m,weathercode'
                ),
                'timezone': tz_name,
                'start_date': day.isoformat(),
                'end_date': day.isoformat(),
            },
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
        h = data.get('hourly') or {}
        if not h.get('time'):
            return None
        return h
    except Exception as exc:
        logger.warning('Open-Meteo digest: %s', exc)
        return None


def _aggregate_periods(hourly: dict[str, Any], tz_name: str) -> dict[str, dict[str, Any]]:
    times = hourly.get('time') or []
    temps = hourly.get('temperature_2m') or []
    hums = hourly.get('relative_humidity_2m') or []
    press = hourly.get('pressure_msl') or []
    wspd = hourly.get('wind_speed_10m') or []
    wdir = hourly.get('wind_direction_10m') or []
    codes = hourly.get('weathercode') or []

    buckets: dict[str, list[int]] = {'mor': [], 'day': [], 'eve': [], 'night': []}
    for i, ts in enumerate(times):
        try:
            # "2026-03-03T07:00" или с секундами
            local = dt.datetime.fromisoformat(str(ts))
            if local.tzinfo is None:
                local = local.replace(tzinfo=ZoneInfo(tz_name))
            h = local.hour
        except Exception:
            continue
        buckets[_bucket_key(h)].append(i)

    out: dict[str, dict[str, Any]] = {}
    for key, _ru1, _ru2 in PERIOD_LABELS:
        idxs = buckets.get(key) or []
        if not idxs:
            out[key] = {}
            continue
        ts = [float(temps[i]) for i in idxs if i < len(temps) and temps[i] is not None]
        hs = [float(hums[i]) for i in idxs if i < len(hums) and hums[i] is not None]
        ps = [float(press[i]) for i in idxs if i < len(press) and press[i] is not None]
        ws = [float(wspd[i]) for i in idxs if i < len(wspd) and wspd[i] is not None]
        wd = [float(wdir[i]) for i in idxs if i < len(wdir) and wdir[i] is not None]
        cs = [codes[i] for i in idxs if i < len(codes) and codes[i] is not None]

        tmin = int(round(min(ts))) if ts else 0
        tmax = int(round(max(ts))) if ts else 0
        havg = int(round(sum(hs) / len(hs))) if hs else 0
        pavg = _mmhg(sum(ps) / len(ps)) if ps else 0
        wavg = _ms(sum(ws) / len(ws)) if ws else 0.0
        wdavg = sum(wd) / len(wd) if wd else None
        mode_code = Counter(int(c) for c in cs).most_common(1)[0][0] if cs else None

        out[key] = {
            'tmin': tmin,
            'tmax': tmax,
            'hum': havg,
            'mm': pavg,
            'wms': wavg,
            'wdir': _wind_dir_ru(wdavg),
            'wx': _wmo_word(mode_code),
        }
    return out


def fetch_sun_local(lat: float, lon: float, day: dt.date, tz_name: str) -> tuple[str, str] | None:
    try:
        r = requests.get(
            'https://api.sunrise-sunset.org/json',
            params={'lat': lat, 'lng': lon, 'date': day.isoformat(), 'formatted': '0'},
            timeout=25,
        )
        r.raise_for_status()
        res = r.json().get('results') or {}
        sr = res.get('sunrise')
        ss = res.get('sunset')
        if not sr or not ss:
            return None
        tz = ZoneInfo(tz_name)

        def _to_hm(s: str) -> str:
            u = dt.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
            if u.tzinfo is None:
                u = u.replace(tzinfo=dt.timezone.utc)
            return u.astimezone(tz).strftime('%H:%M')

        return _to_hm(sr), _to_hm(ss)
    except Exception as exc:
        logger.warning('Sunrise-sunset digest: %s', exc)
        return None


def format_holidays(country_code: str, day: dt.date) -> str:
    try:
        import holidays as hol

        cc = (country_code or 'RU').upper()
        try:
            cal = hol.country_holidays(cc, years=day.year)
        except Exception:
            cal = hol.Russia(years=day.year)
        names = [str(nm) for d0, nm in cal.items() if d0 == day and nm]
        lines = [f'     🎊 {n}' for n in names if n]
        return '\n'.join(lines)
    except Exception as exc:
        logger.warning('holidays digest: %s', exc)
        return ''


def _strip_json_fence(raw: str) -> str:
    s = (raw or '').strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s*```$', '', s)
    return s.strip()


def _parse_ai_digest_json(data: dict) -> dict:
    """Плоские строки + вложенный horoscope_by_sign (все знаки)."""
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for k, v in data.items():
        sk = str(k)
        if sk == 'horoscope_by_sign' and isinstance(v, dict):
            out[sk] = {
                str(xk).strip().lower(): str(xv).strip()
                for xk, xv in v.items()
                if str(xv or '').strip()
            }
        elif isinstance(v, dict):
            continue
        elif isinstance(v, list):
            continue
        else:
            out[sk] = str(v).strip()
    return out


def fetch_ai_blocks(
    *,
    date_str: str,
    sign_key: str,
    api_key: str,
) -> dict:
    from django.conf import settings

    from parsing.deepseek_snippet import build_deepseek_client

    sign_key = (sign_key or 'general').strip() or 'general'
    sign_label = ZODIAC_LABELS.get(sign_key, sign_key)

    if sign_key == 'general':
        user = (
            f'Дата: {date_str}. Нужен утренний дайджест для аудитории в России.\n'
            'Верни один JSON-объект с ключами: '
            'quote_ru, quote_author, english_word, ipa, gloss_ru, horoscope_by_sign.\n'
            'quote_ru — короткая жизнеутверждающая цитата на русском (1–2 предложения); '
            'quote_author — автор; english_word — одно слово для изучения; ipa — транскрипция IPA (латиница); '
            'gloss_ru — краткий перевод через « / ».\n'
            'horoscope_by_sign — объект: ровно ключи aries, taurus, gemini, cancer, leo, virgo, libra, '
            'scorpio, sagittarius, capricorn, aquarius, pisces. '
            'Каждое значение — 1–2 предложения на русском: краткий гороскоп на сегодня для этого знака зодиака, '
            'спокойный доброжелательный тон, без катастроф и медицинских советов.\n'
            'Только JSON, без markdown.'
        )
        max_tokens = 4500
    else:
        user = (
            f'Дата: {date_str}. Знак зодиака для гороскопа: {sign_label} (ключ {sign_key}).\n'
            'Верни один JSON-объект с ключами: '
            'quote_ru, quote_author, english_word, ipa, gloss_ru, horoscope_ru.\n'
            'quote_ru — короткая жизнеутверждающая цитата на русском (1–2 предложения), '
            'quote_author — автор; english_word — одно слово для изучения; ipa — транскрипция IPA (латиница); '
            'gloss_ru — краткий перевод через « / »; '
            f'horoscope_ru — 2–4 предложения на русском для знака «{sign_label}», спокойный тон, без катастроф.\n'
            'Только JSON, без markdown.'
        )
        max_tokens = 1200

    client = build_deepseek_client(api_key)
    model = getattr(settings, 'DEEPSEEK_MODEL', 'deepseek-chat')
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': 'Отвечай только валидным JSON-объектом, без текста вне JSON.'},
            {'role': 'user', 'content': user},
        ],
        max_tokens=max_tokens,
        temperature=0.65,
    )
    raw = (resp.choices[0].message.content or '').strip()
    data = json.loads(_strip_json_fence(raw))
    return _parse_ai_digest_json(data if isinstance(data, dict) else {})


def static_horoscope_by_sign_fallback() -> dict[str, str]:
    """Если AI недоступен — нейтральная строка на каждый знак."""
    line = (
        'День подходит для спокойных решений и заботы о себе; не распыляйтесь на споры — '
        'лучше завершить одно дело до конца.'
    )
    return {key: line for key, _, _ in ZODIAC_ORDER}


def static_ai_fallback() -> dict[str, str]:
    return {
        'quote_ru': 'Делай сегодня шаг к тому, что для тебя по-настоящему важно.',
        'quote_author': 'Народная мудрость',
        'english_word': 'space',
        'ipa': 'speɪs',
        'gloss_ru': 'пространство / космос',
        'horoscope_ru': 'Сегодня хороший день для спокойных решений и заботы о себе. '
        'Не распыляйтесь на споры — лучше завершить одно дело до конца.',
    }


def geocode_place_label(query: str) -> tuple[float | None, float | None]:
    """OpenStreetMap Nominatim (нужен осмысленный User-Agent по правилам сервиса)."""
    q = (query or '').strip()
    if len(q) < 2:
        return None, None
    try:
        r = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': q, 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'ProChannelsMorningDigest/1.0'},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None, None
        return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as exc:
        logger.warning('Nominatim digest: %s', exc)
        return None, None


def _digest_fallback_image_jpeg(seed: str) -> bytes:
    """Локальная картинка без внешних CDN — для хостинга, где picsum недоступен."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    w, h = 1200, 800
    base = (248, 250, 253)
    im = Image.new('RGB', (w, h), color=base)
    draw = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(h - 1, 1)
        c = (
            int(240 + 8 * t),
            int(244 + 6 * t),
            int(252 - 4 * t),
        )
        draw.line([(0, y), (w, y)], fill=c)

    font_paths = [
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'),
    ]
    font_lg = font_sm = None
    for fp in font_paths:
        if fp.is_file():
            try:
                font_lg = ImageFont.truetype(str(fp), 44)
                font_sm = ImageFont.truetype(str(fp), 26)
                break
            except Exception:
                pass
    if font_lg is None:
        font_lg = font_sm = ImageFont.load_default()

    title = 'Утренний дайджест'
    sub = (seed or '')[:90]
    tw, th = draw.textbbox((0, 0), title, font=font_lg)[2:]
    draw.text(((w - tw) // 2, h // 2 - 50), title, fill=(35, 48, 62), font=font_lg)
    if sub:
        sw, sh = draw.textbbox((0, 0), sub, font=font_sm)[2:]
        draw.text(((w - sw) // 2, h // 2 + 20), sub, fill=(75, 88, 102), font=font_sm)
    note = 'Локальная заглушка (внешний сервис картинок недоступен)'
    nw, nh = draw.textbbox((0, 0), note, font=font_sm)[2:]
    draw.text(((w - nw) // 2, h - 48), note, fill=(120, 130, 145), font=font_sm)

    buf = io.BytesIO()
    im.save(buf, format='JPEG', quality=88, optimize=True)
    return buf.getvalue()


def download_digest_image_bytes(seed: str) -> bytes | None:
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', seed)[:80]
    url = f'https://picsum.photos/seed/{safe}/1200/800'
    headers = {'User-Agent': 'ProChannelsMorningDigest/1.0 (+https://prochannels.ru)'}

    try:
        from core.telegram_bot_request import telegram_bot_requests_proxies

        proxies = telegram_bot_requests_proxies()
    except Exception:
        proxies = None

    try:
        r = requests.get(
            url,
            timeout=45,
            allow_redirects=True,
            headers=headers,
            proxies=proxies,
        )
        r.raise_for_status()
        data = r.content or b''
        if len(data) > 512:
            return data
        logger.warning('digest image: picsum ответ слишком короткий (%s байт)', len(data))
    except Exception as exc:
        logger.warning('digest image (picsum): %s', exc)

    try:
        out = _digest_fallback_image_jpeg(seed)
        logger.info('digest image: использована локальная заглушка Pillow (seed=%s)', safe[:40])
        return out
    except Exception as exc:
        logger.warning('digest image (fallback Pillow): %s', exc)
        return None


def _local_seconds_since_midnight(when: dt.datetime) -> int:
    return when.hour * 3600 + when.minute * 60 + when.second


def _send_time_to_seconds(t: dt.time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def _digest_local_time_in_window(local_now: dt.datetime, send_time: dt.time, window_sec: int) -> bool:
    """
    True, если локальное время попало в [send_time; send_time + window], с учётом перехода через полночь.

    Для окон, которые переходят на следующий календарный день, теоретически возможен второй проход
    после полуночи (last_sent_on по датам). Для утреннего слота (например 05:00–05:15) это не применимо.
    """
    cur = _local_seconds_since_midnight(local_now)
    start = _send_time_to_seconds(send_time)
    end = start + window_sec
    if end < 86400:
        return start <= cur <= end
    spill_end = end - 86400
    return cur >= start or cur <= spill_end


def is_digest_due_now(cfg, local_now: dt.datetime) -> bool:
    """
    Окно отправки: [send_time; send_time + N сек], раз в сутки по локальной дате.

    N задаётся MORNING_DIGEST_DUE_WINDOW_SEC (по умолчанию 900 с ≈ 15 мин): если тик Celery
    отстаёт в общей очереди, 6 минут было недостаточно.
    """
    if not cfg.is_enabled:
        return False
    wd = local_now.weekday()
    days = cfg.weekdays if isinstance(cfg.weekdays, list) else []
    if days:
        wanted = []
        for x in days:
            try:
                wanted.append(int(x))
            except (TypeError, ValueError):
                continue
        if wanted and wd not in wanted:
            return False
    if cfg.last_sent_on == local_now.date():
        return False
    st = cfg.send_time
    window = int(getattr(settings, 'MORNING_DIGEST_DUE_WINDOW_SEC', 900) or 900)
    window = max(300, min(window, 7200))
    return _digest_local_time_in_window(local_now, st, window)


def compose_digest_text(cfg, *, local_now: dt.datetime, day: dt.date, lat: float, lon: float) -> tuple[str, str]:
    from core.models import get_global_api_keys

    tz_name = cfg.timezone_name or 'Europe/Moscow'
    date_fmt = day.strftime('%d.%m.%Y')

    blocks_plain: list[str] = []
    blocks_html: list[str] = []

    def add_block(plain_part: str, html_part: str) -> None:
        pt = (plain_part or '').strip()
        ht = (html_part or '').strip()
        if pt:
            blocks_plain.append(pt)
        if ht:
            blocks_html.append(ht)

    def _weather_line_html(line: str) -> str:
        return html_escape(line).replace(' ', '&nbsp;')

    if cfg.block_date:
        block = f'🗓 {date_fmt} года'
        add_block(block, f'<b>{html_escape(block)}</b>')

    hourly = None
    if cfg.block_weather:
        hourly = fetch_open_meteo_day(lat, lon, tz_name, day)
        if hourly:
            agg = _aggregate_periods(hourly, tz_name)
            loc = (cfg.location_label or '').strip()
            head = '🌤 Погода на сегодня'
            if loc:
                head += f' ({loc})'
            head += ':'
            plines = [head]
            hlines = [f'<b>{html_escape(head)}</b>']
            for key, _cap, low in PERIOD_LABELS:
                a = agg.get(key) or {}
                if not a:
                    line = f'     ☁️ {low.capitalize()}: данные недоступны'
                    plines.append(line)
                    hlines.append(_weather_line_html(line))
                    continue
                t1, t2 = a['tmin'], a['tmax']
                sign = '−' if t1 < 0 else ''
                sign2 = '−' if t2 < 0 else ''
                t1s = f'{sign}{abs(t1)}' if t1 != 0 or t2 == 0 else str(t1)
                t2s = f'{sign2}{abs(t2)}' if t2 != 0 else str(t2)
                line = (
                    f'     🌡️ {low.capitalize()}: от {t1s} до {t2s} °C | {a.get("wx", "—")} | '
                    f'{a.get("mm", 0)} мм рт.ст. | {a.get("hum", 0)}% | '
                    f'{a.get("wms", 0)} м/с, {a.get("wdir", "—")}'
                )
                plines.append(line)
                hlines.append(_weather_line_html(line))
            add_block('\n'.join(plines), '\n'.join(hlines))
        else:
            msg = '⛅ Погода: не удалось загрузить прогноз.'
            add_block(msg, f'<b>{html_escape(msg)}</b>')

    if cfg.block_sun:
        sun = fetch_sun_local(lat, lon, day, tz_name)
        if sun:
            sr, ss = sun
            head = '🌅 Рассвет и закат сегодня'
            plines = [head, f'     🌄 Рассвет: {sr}', f'     🌇 Закат: {ss}']
            hlines = [
                f'<b>{html_escape(head)}</b>',
                f'     🌄 Рассвет: {html_escape(sr)}',
                f'     🌇 Закат: {html_escape(ss)}',
            ]
            add_block('\n'.join(plines), '\n'.join(hlines))
        else:
            msg = '🌅 Солнце: данные недоступны.'
            add_block(msg, f'<b>{html_escape(msg)}</b>')

    keys = get_global_api_keys()
    api_key = (keys.get_deepseek_api_key() or '').strip()
    want_ai = (
        (cfg.use_ai_quote and cfg.block_quote)
        or (cfg.use_ai_english and cfg.block_english)
        or (cfg.use_ai_horoscope and cfg.block_horoscope)
    )
    ai: dict[str, str] = {}
    if want_ai and api_key:
        try:
            ai = fetch_ai_blocks(
                date_str=date_fmt,
                sign_key=cfg.horoscope_sign or 'general',
                api_key=api_key,
            )
        except Exception as exc:
            logger.warning('DeepSeek digest: %s', exc)
            ai = {}
    fb = static_ai_fallback()

    if cfg.block_quote:
        head = '💬 Цитата дня'
        if cfg.use_ai_quote and api_key and (ai.get('quote_ru') or '').strip():
            q = ai['quote_ru'].strip()
            au = (ai.get('quote_author') or '').strip() or '—'
        else:
            q, au = fb['quote_ru'], fb['quote_author']
        body_plain = f'     {q} ({au})'
        body_html = f'     {html_escape(q)} ({html_escape(au)})'
        add_block(f'{head}\n{body_plain}', f'<b>{html_escape(head)}</b>\n{body_html}')

    if cfg.block_english:
        head = '📖 Английское слово'
        if cfg.use_ai_english and api_key and (ai.get('english_word') or '').strip():
            w = ai['english_word'].strip()
            ipa = (ai.get('ipa') or '—').strip()
            gl = (ai.get('gloss_ru') or '—').strip()
        else:
            w, ipa, gl = fb['english_word'], fb['ipa'], fb['gloss_ru']
        body_plain = f'     {w} ({ipa}) — {gl}'
        body_html = f'     {html_escape(w)} ({html_escape(ipa)}) — {html_escape(gl)}'
        add_block(f'{head}\n{body_plain}', f'<b>{html_escape(head)}</b>\n{body_html}')

    if cfg.block_holidays:
        hol = format_holidays(cfg.country_for_holidays, day)
        head = '🎉 Праздники сегодня'
        if hol:
            add_block(
                f'{head}:\n{hol}',
                f'<b>{html_escape(head)}:</b>\n' + _weather_line_html(hol),
            )
        else:
            sub = '     ✨ Официальных праздников по календарю нет'
            add_block(
                f'{head}:\n{sub}',
                f'<b>{html_escape(head)}:</b>\n{html_escape(sub)}',
            )

    if cfg.block_horoscope:
        sign_cfg = (cfg.horoscope_sign or 'general').strip() or 'general'
        fb_hs = static_horoscope_by_sign_fallback()

        if sign_cfg == 'general':
            head = '✨ Гороскоп на сегодня (по знакам зодиака)'
            hmap = ai.get('horoscope_by_sign') if isinstance(ai.get('horoscope_by_sign'), dict) else {}
            plines = [head]
            hlines = [f'<b>{html_escape(head)}</b>']
            for key, emoji, label in ZODIAC_ORDER:
                raw_txt = (hmap.get(key) or hmap.get(key.lower()) or '').strip()
                if cfg.use_ai_horoscope and api_key and raw_txt:
                    txt = raw_txt
                else:
                    txt = fb_hs.get(key) or fb['horoscope_ru']
                plines.append(f'     {emoji} {label} — {txt}')
                hlines.append(
                    f'     {emoji} <b>{html_escape(label)}</b> — {html_escape(txt)}'
                )
            add_block('\n'.join(plines), '\n'.join(hlines))
        else:
            label = ZODIAC_LABELS.get(sign_cfg, sign_cfg)
            head = f'✨ Гороскоп на сегодня: {label}'
            if cfg.use_ai_horoscope and api_key and (ai.get('horoscope_ru') or '').strip():
                ho = ai['horoscope_ru'].strip()
            else:
                ho = fb['horoscope_ru']
            body_plain = f'     {ho}'
            body_html = f'     {html_escape(ho)}'
            add_block(f'{head}\n{body_plain}', f'<b>{html_escape(head)}</b>\n{body_html}')

    plain = '\n\n'.join(blocks_plain).strip()
    html = '\n\n'.join(blocks_html).strip()
    return plain, html


def _create_morning_digest_draft_post(cfg, *, day: dt.date, local_now: dt.datetime):
    """
    Собирает текст/медиа и создаёт черновик поста, привязанный к каналу.
    Используется и по расписанию, и по кнопке «создать сейчас».
    """
    from content.models import Post, PostMedia, normalize_post_media_orders

    channel = cfg.channel
    lat = float(cfg.latitude)
    lon = float(cfg.longitude)
    plain, html = compose_digest_text(cfg, local_now=local_now, day=day, lat=lat, lon=lon)

    post = Post.objects.create(
        author=channel.owner,
        text=plain,
        text_html=html,
        status=Post.STATUS_DRAFT,
        ord_label='',
    )
    post.channels.add(channel)

    if cfg.block_image:
        seed = f'{day.isoformat()}-{channel.pk}-{cfg.image_seed_extra or "d"}'
        blob = download_digest_image_bytes(seed)
        if blob:
            PostMedia.objects.create(
                post=post,
                file=ContentFile(blob, name=f'digest_{day.isoformat()}.jpg'),
                media_type=PostMedia.TYPE_PHOTO,
                order=1,
            )
            normalize_post_media_orders(post)

    return post


def create_morning_digest_draft_now(cfg_id: int) -> tuple[bool, str]:
    """
    Ручная генерация черновика (без проверки окна времени и без автопубликации).
    Не меняет last_sent_on — расписание по-прежнему срабатывает в своё время.
    """
    from .models import ChannelMorningDigest

    try:
        cfg = ChannelMorningDigest.objects.select_related('channel', 'channel__owner').get(pk=cfg_id)
    except ChannelMorningDigest.DoesNotExist:
        return False, 'Настройки дайджеста не найдены.'

    channel = cfg.channel
    if not channel.is_active:
        return False, 'Канал неактивен — черновик не создан.'

    lock_key = f'morning_digest_manual:{cfg_id}'
    if not cache.add(lock_key, '1', timeout=90):
        return False, 'Генерация уже запущена, подождите немного.'

    try:
        tz = ZoneInfo(cfg.timezone_name or 'Europe/Moscow')
        local_now = timezone.now().astimezone(tz)
        day = local_now.date()
        try:
            post = _create_morning_digest_draft_post(cfg, day=day, local_now=local_now)
        except (TypeError, ValueError) as exc:
            logger.warning('morning digest manual cfg=%s bad coords: %s', cfg_id, exc)
            return False, 'Проверьте широту и долготу в настройках.'
        except Exception:
            logger.exception('morning digest manual cfg=%s', cfg_id)
            return False, 'Не удалось собрать дайджест (сеть или внешние API). Повторите позже.'
        return True, f'Черновик дайджеста создан (пост №{post.pk}). Откройте раздел постов.'
    finally:
        cache.delete(lock_key)


def publish_morning_digest(cfg_id: int) -> bool:
    """
    Один раз за локальные сутки: блокировка строки ChannelMorningDigest (select_for_update),
    без Redis-ключа до успеха. Раньше cache.add до создания поста мог «залипнуть» на сутки при обрыве
    воркера — тогда слот не повторялся и last_sent_on не обновлялся.

    Возвращает True, если создан черновик и обновлён last_sent_on.
    """
    from django.db import transaction

    from content.tasks import publish_post_task

    from .models import ChannelMorningDigest

    with transaction.atomic():
        cfg = (
            ChannelMorningDigest.objects.select_related('channel', 'channel__owner')
            .select_for_update()
            .get(pk=cfg_id)
        )
        if not cfg.is_enabled:
            return False

        channel = cfg.channel
        tz = ZoneInfo(cfg.timezone_name or 'Europe/Moscow')
        local_now = timezone.now().astimezone(tz)
        day = local_now.date()

        if not is_digest_due_now(cfg, local_now):
            return False
        if not channel.is_active:
            logger.info('morning digest cfg=%s: канал неактивен — пропуск', cfg_id)
            return False

        post = _create_morning_digest_draft_post(cfg, day=day, local_now=local_now)

        cfg.last_sent_on = day
        cfg.save(update_fields=['last_sent_on', 'updated_at'])

        if channel.token_configured:
            pid = post.pk

            def _enqueue():
                publish_post_task.delay(pid)

            transaction.on_commit(_enqueue)
        else:
            logger.info(
                'morning digest cfg=%s: черновик #%s без автопубликации (канал без токена)',
                cfg_id,
                post.pk,
            )

        logger.info(
            'morning digest cfg=%s: черновик #%s для канала %s, publish=%s',
            cfg_id,
            post.pk,
            channel.pk,
            bool(channel.token_configured),
        )
        return True


def tick_morning_digests() -> None:
    from .models import ChannelMorningDigest

    fired = 0
    for cfg in ChannelMorningDigest.objects.filter(is_enabled=True).select_related('channel'):
        try:
            tz = ZoneInfo(cfg.timezone_name or 'Europe/Moscow')
            local_now = timezone.now().astimezone(tz)
            if not is_digest_due_now(cfg, local_now):
                continue
            if publish_morning_digest(cfg.pk):
                fired += 1
        except Exception:
            logger.exception('morning digest cfg=%s', cfg.pk)
    if fired:
        logger.info('morning_digest_tick: обработано конфигов в окне времени: %s', fired)
