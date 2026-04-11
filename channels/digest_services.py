"""
Утренний дайджест для каналов.

- Погода: Open-Meteo (без ключа).
- Солнце: sunrise-sunset.org (без ключа).
- Праздники: библиотека holidays + для РФ доп. фиксированные даты и переносимые православные (Пасха, Троица и др.).
- Сводка по вчерашним постам канала: при включённом блоке и ключе DeepSeek — одно предложение; иначе краткий fallback.
- Цитата / слово / гороскоп: DeepSeek (текст), если включено и задан ключ в «Ключи API».
  Гороскоп — один общий текст на всех (в том же посте, что и остальные блоки).
- Картинка: picsum.photos по seed (seed зависит от сезона, погоды по прогнозу Open-Meteo и времени генерации);
  при недоступности — локальный JPEG в Pillow с градиентом под сезон и погоду.
  На картинку накладывается полупрозрачный водяной знак с названием канала (красивый шрифт при наличии в системе).
  Если задан HTTP/SOCKS-прокси в «Ключи API», скачивание picsum идёт через него.
"""
from __future__ import annotations

import colorsys
import datetime as dt
import hashlib
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

PERIOD_LABELS = [
    ('mor', 'Утром', 'утром', '🌅'),
    ('day', 'Днём', 'днём', '☀️'),
    ('eve', 'Вечером', 'вечером', '🌇'),
    ('night', 'Ночью', 'ночью', '🌙'),
]


def _digest_temp_span(tmin: int, tmax: int) -> str:
    def one(t: int) -> str:
        if t < 0:
            return f'−{abs(t)}'
        return str(t)

    if tmin == tmax:
        return f'{one(tmin)} °C'
    return f'{one(tmin)}–{one(tmax)} °C'


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
    for key, _ru1, _ru2, _ico in PERIOD_LABELS:
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


# Памятные и профессиональные даты РФ (фиксированный календарь).
# Дополняют ответ библиотеки holidays; переносимые православные даты задаются отдельно (см. _ru_movable_observance_names).
RU_EXTRA_OBSERVANCES: dict[tuple[int, int], str] = {
    (1, 13): 'Старый Новый год',
    (1, 19): 'Крещение Господне',
    (1, 21): 'День инженерных войск',
    (1, 25): 'Татьянин день (День российского студенчества)',
    (2, 8): 'День российской науки',
    (2, 15): 'День памяти воинов-интернационалистов',
    (2, 27): 'День Сил специальных операций',
    (3, 1): 'Всемирный день гражданской обороны',
    (3, 12): 'День работников угольной промышленности',
    (3, 19): 'День моряка-подводника',
    (3, 25): 'День работников культуры',
    (3, 29): 'День специалиста юридической службы ВС РФ',
    (4, 1): 'День смеха',
    (4, 12): 'День космонавтики',
    (4, 26): 'День памяти погибших в радиационных авариях и катастрофах',
    (5, 7): 'День радио',
    (5, 8): 'Всемирный день Красного Креста и Красного Полумесяца',
    (5, 21): 'День полярника',
    (5, 24): 'День славянской письменности и культуры',
    (5, 26): 'День российского предпринимательства',
    (5, 31): 'День российской адвокатуры',
    (6, 1): 'Международный день защиты детей',
    (6, 5): 'Всемирный день окружающей среды',
    (6, 8): 'День социального работника',
    (6, 26): 'День изобретателя и рационализатора',
    (7, 1): 'День ветеранов боевых действий',
    (7, 2): 'День российской почты',
    (7, 3): 'День ГИБДД',
    (7, 8): 'День семьи, любви и верности',
    (7, 11): 'День рыбака',
    (7, 17): 'День морской пехоты',
    (7, 28): 'День Крещения Руси',
    (8, 1): 'День тыла Вооружённых Сил',
    (8, 2): 'День ВДВ',
    (8, 9): 'День физкультурника',
    (8, 22): 'День Государственного флага Российской Федерации',
    (8, 27): 'День российского кино',
    (9, 1): 'День знаний',
    (9, 2): 'День окончания Второй мировой войны (1945)',
    (9, 8): 'День Бородинского сражения',
    (9, 13): 'День программиста',
    (9, 19): 'День оружейника',
    (9, 27): 'День воспитателя и дошкольного работника',
    (10, 1): 'Международный день пожилых людей',
    (10, 5): 'День учителя (международный)',
    (10, 25): 'День таможенника',
    (11, 5): 'День военного разведчика',
    (11, 10): 'День сотрудника органов внутренних дел',
    (11, 13): 'День войск радиационной, химической и биологической защиты',
    (11, 19): 'День ракетных войск и артиллерии',
    (11, 30): 'День защиты информации',
    (12, 3): 'День Неизвестного солдата',
    (12, 12): 'День Конституции Российской Федерации',
    (12, 19): 'День работника органов государственной безопасности',
    (12, 20): 'День работника органов безопасности Российской Федерации',
}


def _ru_extra_observance_names(day: dt.date) -> list[str]:
    key = (day.month, day.day)
    label = RU_EXTRA_OBSERVANCES.get(key)
    return [label] if label else []


def orthodox_easter_gregorian(year: int) -> dt.date:
    """
    Дата Пасхи по юлианскому пасхалия в григорианском календаре (как в РПЦ; 1900–2099).
    """
    a = year % 19
    b = year % 4
    c = year % 7
    d = (19 * a + 15) % 30
    e = (2 * b + 4 * c + 6 * d + 6) % 7
    f = d + e + 114
    jm = f // 31  # 3 = март (юл.), 4 = апрель (юл.)
    jd = f % 31 + 1
    if jm == 3:
        offset = jd - 1
    else:
        offset = 31 + (jd - 1)
    anchor = dt.date(year, 3, 14)
    return anchor + dt.timedelta(days=offset)


def _ru_movable_observance_names(day: dt.date) -> list[str]:
    """
    Переносимые даты от православной Пасхи (григорианский календарь, РПЦ).

    Смещения от вычисленной Пасхи: Великий пост, Страстная и Светлая седмицы,
    Пятидесятница, Вознесение, дни после Пятидесятницы — как в типовом богослужебном круге.
    """
    y = day.year
    try:
        pascha = orthodox_easter_gregorian(y)
    except Exception:
        return []
    out: list[str] = []
    # До Пасхи: подготовка, Великий пост, Страстная седмица
    proshenoe = pascha - dt.timedelta(days=49)
    chistyi_pon = pascha - dt.timedelta(days=48)
    lazareva = pascha - dt.timedelta(days=8)
    verb = pascha - dt.timedelta(days=7)
    velikiy_chetverg = pascha - dt.timedelta(days=3)
    strastnaya_pyat = pascha - dt.timedelta(days=2)
    velikaya_subbota = pascha - dt.timedelta(days=1)
    # После Пасхи: Светлая седмица, Пятидесятница, Вознесение
    fomino = pascha + dt.timedelta(days=7)
    radonitsa = pascha + dt.timedelta(days=9)
    otdanie_paschi = pascha + dt.timedelta(days=38)
    voznesenie = pascha + dt.timedelta(days=39)
    troitsa = pascha + dt.timedelta(days=49)
    den_svyatogo_duha = pascha + dt.timedelta(days=50)
    nedelya_vsekh_svyatykh = pascha + dt.timedelta(days=56)
    nachalo_petrova_posta = pascha + dt.timedelta(days=57)

    if day == proshenoe:
        out.append('Прощёное воскресенье')
    if day == chistyi_pon:
        out.append('Начало Великого поста (Чистый понедельник)')
    if day == lazareva:
        out.append('Лазарева суббота')
    if day == verb:
        out.append('Вербное воскресенье (Вход Господень в Иерусалим)')
    if day == velikiy_chetverg:
        out.append('Великий четверг')
    if day == strastnaya_pyat:
        out.append('Страстная Пятница')
    if day == velikaya_subbota:
        out.append('Великая суббота')
    if day == pascha:
        out.append('Пасха (Светлое Христово Воскресенье)')
    if day == fomino:
        out.append('Фомино воскресенье (неделя 1-я по Пасхе)')
    if day == radonitsa:
        out.append('Радоница')
    if day == otdanie_paschi:
        out.append('Отдание Пасхи')
    if day == voznesenie:
        out.append('Вознесение Господне')
    if day == troitsa:
        out.append('Троица (День Святой Троицы, Пятидесятница)')
    if day == den_svyatogo_duha:
        out.append('День Святого Духа')
    if day == nedelya_vsekh_svyatykh:
        out.append('Неделя всех святых (1-я по Пятидесятнице)')
    if day == nachalo_petrova_posta:
        out.append('Начало Петрова поста')
    return out


def format_holidays(country_code: str, day: dt.date) -> str:
    try:
        import holidays as hol

        cc = (country_code or 'RU').upper()
        if cc == 'RU':
            try:
                cal = hol.Russia(years=day.year, language='ru')
            except TypeError:
                cal = hol.Russia(years=day.year)
        else:
            try:
                cal = hol.country_holidays(cc, years=day.year, language='ru')
            except TypeError:
                cal = hol.country_holidays(cc, years=day.year)

        names_lib = [str(nm) for d0, nm in cal.items() if d0 == day and nm]
        hol_lower = {n.strip().lower() for n in names_lib}

        extras: list[str] = []
        if cc == 'RU':
            for ex in _ru_extra_observance_names(day):
                if ex.strip().lower() not in hol_lower:
                    extras.append(ex)
            for ex in _ru_movable_observance_names(day):
                if ex.strip().lower() not in hol_lower:
                    extras.append(ex)

        seen: set[str] = set()
        ordered: list[str] = []
        for n in names_lib + extras:
            k = n.strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            ordered.append(n.strip())

        extra_set = {e.strip() for e in extras}
        lines = []
        for n in ordered:
            icon = '📌' if n in extra_set else '🎊'
            lines.append(f'     {icon} {n}')
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
    """Плоские строки из JSON ответа DeepSeek."""
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for k, v in data.items():
        if isinstance(v, dict):
            continue
        if isinstance(v, list):
            continue
        out[str(k)] = str(v).strip()
    return out


def fetch_ai_blocks(
    *,
    date_str: str,
    api_key: str,
) -> dict:
    from django.conf import settings

    from parsing.deepseek_snippet import build_deepseek_client

    user = (
        f'Дата: {date_str}. Нужен утренний дайджест для аудитории в России.\n'
        'Верни один JSON-объект с ключами: '
        'quote_ru, quote_author, english_word, ipa, gloss_ru, horoscope_unified.\n'
        'quote_ru — короткая жизнеутверждающая цитата на русском (1–2 предложения); '
        'quote_author — автор; english_word — одно слово для изучения; ipa — транскрипция IPA (латиница); '
        'gloss_ru — краткий перевод через « / ».\n'
        'horoscope_unified — 3–6 предложений на русском: один общий гороскоп на сегодня для всех читателей '
        '(не по знакам зодиака, без списка знаков), спокойный доброжелательный тон, без катастроф и медицинских советов.\n'
        'Только JSON, без markdown.'
    )
    max_tokens = 3500

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


def static_ai_fallback() -> dict[str, str]:
    _ho = (
        'Сегодня хороший день для спокойных решений и заботы о себе. '
        'Не распыляйтесь на споры — лучше завершить одно дело до конца.'
    )
    return {
        'quote_ru': 'Делай сегодня шаг к тому, что для тебя по-настоящему важно.',
        'quote_author': 'Народная мудрость',
        'english_word': 'space',
        'ipa': 'speɪs',
        'gloss_ru': 'пространство / космос',
        'horoscope_ru': _ho,
        'horoscope_unified': _ho,
    }


def _ru_publication_count_word(n: int) -> str:
    if 11 <= (n % 100) <= 14:
        return 'публикаций'
    r = n % 10
    if r == 1:
        return 'публикация'
    if r in (2, 3, 4):
        return 'публикации'
    return 'публикаций'


def fetch_yesterday_channel_news_one_liner(
    *,
    channel,
    yesterday: dt.date,
    tz_name: str,
    api_key: str,
    use_ai: bool,
) -> str:
    """Одно предложение о вчерашних постах канала в локальных сутках tz_name."""
    from content.models import Post

    tz = ZoneInfo(tz_name or 'Europe/Moscow')
    start = dt.datetime.combine(yesterday, dt.time.min, tzinfo=tz)
    end = start + dt.timedelta(days=1)
    qs = (
        Post.objects.filter(
            channels=channel,
            status=Post.STATUS_PUBLISHED,
            published_at__gte=start,
            published_at__lt=end,
        )
        .distinct()
        .order_by('-published_at')
    )
    n = qs.count()
    if n == 0:
        return 'Вчера в канале не выходило новых публикаций.'

    w = _ru_publication_count_word(n)
    if not use_ai or not (api_key or '').strip():
        return f'Вчера в канале вышло {n} {w}.'

    from django.conf import settings

    from parsing.deepseek_snippet import build_deepseek_client

    lines: list[str] = []
    for p in qs[:18]:
        t = (getattr(p, 'text', None) or '').strip().replace('\n', ' ')
        if not t:
            continue
        if len(t) > 240:
            t = t[:240] + '…'
        lines.append(t)
    if not lines:
        return f'Вчера в канале вышло {n} {w}.'

    bullet = '\n'.join(f'- {x}' for x in lines[:15])
    user = (
        f'За {yesterday.strftime("%d.%m.%Y")} в телеграм-канале вышло {n} {w}. '
        f'Фрагменты текстов:\n{bullet}\n\n'
        'Напиши ровно одно короткое нейтральное предложение на русском: о чём шла речь / что освещалось. '
        'Без оценок и призывов, без заголовка и кавычек — только это предложение.'
    )
    try:
        client = build_deepseek_client(api_key)
        model = getattr(settings, 'DEEPSEEK_MODEL', 'deepseek-chat')
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': 'Отвечай одним предложением на русском, без списков и markdown.'},
                {'role': 'user', 'content': user},
            ],
            max_tokens=220,
            temperature=0.35,
        )
        raw = (resp.choices[0].message.content or '').strip()
        raw = raw.replace('\n', ' ').strip()
        if raw:
            return raw
    except Exception as exc:
        logger.warning('DeepSeek yesterday digest: %s', exc)
    return f'Вчера в канале вышло {n} {w}.'


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


def _digest_season_north(day: dt.date) -> str:
    """Время года для РФ / северного полушария по календарю."""
    m = day.month
    if m in (12, 1, 2):
        return 'winter'
    if m in (3, 4, 5):
        return 'spring'
    if m in (6, 7, 8):
        return 'summer'
    return 'autumn'


def _digest_wmo_image_bucket(code: int | None) -> str:
    """Грубый класс погоды для подбора картинки/градиента (WMO weathercode)."""
    if code is None:
        return 'unknown'
    try:
        c = int(code)
    except (TypeError, ValueError):
        return 'unknown'
    if c <= 1:
        return 'clear'
    if c <= 3:
        return 'cloudy'
    if 45 <= c <= 48:
        return 'fog'
    if c < 50:
        return 'cloudy'
    if c <= 67 or c in (80, 81, 82):
        return 'rain'
    if 95 <= c <= 99:
        return 'storm'
    if 71 <= c <= 77 or c in (85, 86):
        return 'snow'
    return 'cloudy'


def _digest_temp_band(tavg: float) -> str:
    if tavg < -5:
        return 'freezing'
    if tavg < 5:
        return 'cold'
    if tavg < 13:
        return 'cool'
    if tavg < 22:
        return 'mild'
    if tavg < 30:
        return 'warm'
    return 'hot'


def _digest_image_weather_context(
    day: dt.date, lat: float, lon: float, tz_name: str
) -> dict[str, Any]:
    """
    Сезон (север) + доминирующий тип погоды и средняя температура за сутки по Open-Meteo.
    """
    season = _digest_season_north(day)
    hourly = fetch_open_meteo_day(lat, lon, tz_name, day)
    if not hourly:
        return {
            'season': season,
            'wx_bucket': 'unknown',
            't_band': 'mild',
            'tavg': 10.0,
            'mode_wmo': None,
        }
    codes = hourly.get('weathercode') or []
    temps = hourly.get('temperature_2m') or []
    ints: list[int] = []
    for c in codes:
        try:
            ints.append(int(c))
        except (TypeError, ValueError):
            continue
    mode_wmo = Counter(ints).most_common(1)[0][0] if ints else None
    wx_bucket = _digest_wmo_image_bucket(mode_wmo)
    tvals = [float(t) for t in temps if t is not None]
    tavg = sum(tvals) / len(tvals) if tvals else 10.0
    t_band = _digest_temp_band(tavg)
    return {
        'season': season,
        'wx_bucket': wx_bucket,
        't_band': t_band,
        'tavg': tavg,
        'mode_wmo': mode_wmo,
    }


def _digest_build_image_seed(
    *,
    day: dt.date,
    channel_pk: int,
    image_seed_extra: str,
    local_now: dt.datetime,
    wx_ctx: dict[str, Any],
) -> str:
    """
    Seed для picsum: меняется с сезоном, погодой, температурой, часом генерации и каналом.
    """
    extra_s = re.sub(r'[^a-zA-Z0-9_-]', '_', (image_seed_extra or '')[:20])[:20] or 'x'
    hr = local_now.hour
    base = (
        f"{day.isoformat()}-c{channel_pk}-h{hr:02d}-"
        f"{wx_ctx['season']}-{wx_ctx['wx_bucket']}-{wx_ctx['t_band']}-"
        f"t{wx_ctx['tavg']:.1f}-m{wx_ctx['mode_wmo']}-{extra_s}"
    )
    h12 = hashlib.sha256(base.encode('utf-8')).hexdigest()[:12]
    # только [a-zA-Z0-9_-] для picsum path
    raw = f"{base}-{h12}"
    return re.sub(r'[^a-zA-Z0-9_-]', '_', raw)[:80]


def _digest_palette_season_weather(wx_ctx: dict[str, Any], seed_salt: str) -> tuple[tuple[int, int, int], ...]:
    """Палитра градиента заглушки: сезон + погода + температура + щепоть вариации из salt."""
    dig = hashlib.sha256(
        f"{wx_ctx.get('season')}|{wx_ctx.get('wx_bucket')}|{wx_ctx.get('t_band')}|"
        f"{wx_ctx.get('tavg', 0):.2f}|{seed_salt}".encode('utf-8')
    ).digest()
    season = wx_ctx.get('season') or 'spring'
    wxb = wx_ctx.get('wx_bucket') or 'unknown'
    tb = wx_ctx.get('t_band') or 'mild'

    # Базовый оттенок по сезону (HSV hue 0..1)
    sh = {'winter': 0.58, 'spring': 0.30, 'summer': 0.14, 'autumn': 0.08}
    h0 = (sh.get(season, 0.25) + dig[0] / 900.0) % 1.0
    # Погода: дождь/туман — холоднее и серее; ясно — теплее; снег — к синему
    wx_shift = {
        'clear': 0.03,
        'cloudy': -0.02,
        'fog': -0.06,
        'rain': -0.05,
        'storm': -0.08,
        'snow': 0.02,
        'unknown': 0.0,
    }
    h0 = (h0 + wx_shift.get(wxb, -0.03) + dig[1] / 1200.0) % 1.0
    h1 = (h0 + 0.06 + dig[2] / 800.0) % 1.0

    # Насыщенность: лето/ясно ярче; зима/туман приглушённее
    sat0 = 0.10 + (dig[3] % 20) / 350.0
    if season == 'summer' and wxb == 'clear':
        sat0 += 0.12
    if wxb in ('fog', 'cloudy', 'rain'):
        sat0 *= 0.65
    if wxb == 'snow':
        sat0 *= 0.55
    sat0 = max(0.04, min(0.38, sat0))

    # Яркость от «температурной полосы»
    tb_vhi = {
        'freezing': 0.72,
        'cold': 0.76,
        'cool': 0.80,
        'mild': 0.84,
        'warm': 0.88,
        'hot': 0.90,
    }
    v_hi = tb_vhi.get(tb, 0.84) + (dig[4] % 14) / 250.0
    v_lo = v_hi - 0.30 - (dig[5] % 10) / 250.0
    v_hi = max(0.55, min(0.95, v_hi))
    v_lo = max(0.35, min(0.72, v_lo))

    r1, g1, b1 = colorsys.hsv_to_rgb(h0, sat0, v_hi)
    r2, g2, b2 = colorsys.hsv_to_rgb(h1, min(0.42, sat0 + 0.1), v_lo)
    top = (int(r1 * 255), int(g1 * 255), int(b1 * 255))
    bot = (int(r2 * 255), int(g2 * 255), int(b2 * 255))

    th = (h0 + 0.48) % 1.0
    tr, tg, tb_ = colorsys.hsv_to_rgb(th, 0.35, 0.20)
    title_c = (int(tr * 255), int(tg * 255), int(tb_ * 255))
    sh2, ss2, sv2 = (h0 + 0.12) % 1.0, 0.14, 0.36
    sr, sg, sb = colorsys.hsv_to_rgb(sh2, ss2, sv2)
    sub_c = (int(sr * 255), int(sg * 255), int(sb * 255))
    nh, ns, nv = (h0 + 0.32) % 1.0, 0.09, 0.46
    nr, ng, nb = colorsys.hsv_to_rgb(nh, ns, nv)
    note_c = (int(nr * 255), int(ng * 255), int(nb * 255))
    return top, bot, title_c, sub_c, note_c


def _digest_color_tuple_from_seed(seed: str) -> tuple[tuple[int, int, int], ...]:
    """Детерминированная палитра из seed: верх/низ градиента, цвета текста."""
    dig = hashlib.sha256((seed or 'morning').encode('utf-8')).digest()
    h0 = dig[0] / 255.0
    h1 = (h0 + 0.07 + dig[1] / 2000.0) % 1.0
    sat = 0.07 + (dig[2] % 28) / 400.0
    v_hi = 0.82 + (dig[3] % 18) / 200.0
    v_lo = 0.48 + (dig[4] % 22) / 200.0
    r1, g1, b1 = colorsys.hsv_to_rgb(h0, sat, v_hi)
    r2, g2, b2 = colorsys.hsv_to_rgb(h1, min(0.45, sat + 0.12), v_lo)
    top = (int(r1 * 255), int(g1 * 255), int(b1 * 255))
    bot = (int(r2 * 255), int(g2 * 255), int(b2 * 255))
    th, ts, tv = (h0 + 0.52) % 1.0, 0.4, 0.18
    tr, tg, tb = colorsys.hsv_to_rgb(th, ts, tv)
    title_c = (int(tr * 255), int(tg * 255), int(tb * 255))
    sh, ss, sv = (h0 + 0.15) % 1.0, 0.12, 0.38
    sr, sg, sb = colorsys.hsv_to_rgb(sh, ss, sv)
    sub_c = (int(sr * 255), int(sg * 255), int(sb * 255))
    nh, ns, nv = (h0 + 0.35) % 1.0, 0.08, 0.48
    nr, ng, nb = colorsys.hsv_to_rgb(nh, ns, nv)
    note_c = (int(nr * 255), int(ng * 255), int(nb * 255))
    return top, bot, title_c, sub_c, note_c


def _lerp_rgb(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _digest_load_watermark_font(size: int):
    """Шрифт для водяного знака: serif / italic, с поддержкой кириллицы (первый доступный из списка)."""
    from PIL import ImageFont

    candidates = [
        Path('/Library/Fonts/Georgia.ttf'),
        Path('/System/Library/Fonts/Supplemental/Georgia.ttf'),
        Path('/System/Library/Fonts/Supplemental/Georgia Italic.ttf'),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf'),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'),
        Path('/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf'),
        Path('/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf'),
        Path('/usr/share/fonts/truetype/noto/NotoSerif-Italic.ttf'),
        Path('/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf'),
    ]
    for fp in candidates:
        if fp.is_file():
            try:
                return ImageFont.truetype(str(fp), size)
            except Exception:
                continue
    return ImageFont.load_default()


def _digest_apply_channel_watermark_rgb(im, channel_name: str):
    """
    Накладывает название канала в правом нижнем углу: обводка + полупрозрачный светлый текст.
    Якорь anchor='rb' — иначе в Pillow 9+ координаты по умолчанию (базовая линия) уводят текст за кадр.
    Возвращает RGB для сохранения в JPEG.
    """
    from PIL import Image, ImageDraw

    name = (channel_name or '').strip()
    if not name:
        return im.convert('RGB')
    if len(name) > 48:
        name = name[:46] + '…'

    w, h = im.size
    base = im.convert('RGBA')
    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    margin = max(28, min(56, w // 22))
    max_tw = w - 2 * margin
    # Крупный знак: ~7.2% ширины кадра (на 1200px ≈ 86pt, в пределах 44–110)
    size = max(44, min(110, int(w * 0.072)))
    font = None
    while size > 22:
        font = _digest_load_watermark_font(size)
        bbox = draw.textbbox((0, 0), name, font=font, anchor='lt')
        tw = bbox[2] - bbox[0]
        if tw <= max_tw:
            break
        size -= 3

    # Правый нижний угол: (x, y) — правый нижний край строки (Pillow 9+ anchor='rb')
    x, y = w - margin, h - margin
    sw = max(3, min(8, size // 14))
    # stroke_fill только RGB — иначе часть сборок Pillow бросает исключение, и picsum отдаётся без знака
    draw.text(
        (x, y),
        name,
        font=font,
        anchor='rb',
        fill=(255, 255, 255, 255),
        stroke_width=sw,
        stroke_fill=(0, 0, 0),
    )
    # Делаем весь слой текста слегка прозрачным (читаемо на любом фоне)
    r_o, g_o, b_o, a_o = overlay.split()
    a_o = a_o.point(lambda p: min(255, int(p * 0.72)))
    overlay = Image.merge('RGBA', (r_o, g_o, b_o, a_o))
    out = Image.alpha_composite(base, overlay)
    return out.convert('RGB')


def _digest_fallback_image_jpeg(
    seed: str,
    *,
    channel_name: str = '',
    wx_ctx: dict[str, Any] | None = None,
) -> bytes:
    """Локальная картинка без внешних CDN; градиент от сезона и погоды (или от seed, если контекста нет)."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    w, h = 1200, 800
    dig = hashlib.sha256((seed or 'morning').encode('utf-8')).digest()
    if wx_ctx:
        top, bot, title_c, sub_c, note_c = _digest_palette_season_weather(wx_ctx, seed)
    else:
        top, bot, title_c, sub_c, note_c = _digest_color_tuple_from_seed(seed)

    im = Image.new('RGB', (w, h), color=top)
    draw = ImageDraw.Draw(im)
    # Вертикальный градиент + лёгкий сдвиг «полос» по горизонтали (быстро, без перебора всех пикселей)
    bend = (dig[5] % 31) / 5000.0
    for y in range(h):
        ty = y / max(h - 1, 1)
        t = ty + bend * (1.0 - abs(ty - 0.5) * 2)
        t = max(0.0, min(1.0, t))
        c = _lerp_rgb(top, bot, t)
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
    draw.text(((w - tw) // 2, h // 2 - 50), title, fill=title_c, font=font_lg)
    if sub:
        sw, sh = draw.textbbox((0, 0), sub, font=font_sm)[2:]
        draw.text(((w - sw) // 2, h // 2 + 20), sub, fill=sub_c, font=font_sm)
    note = 'Локальная картинка · без внешнего CDN'
    nw, nh = draw.textbbox((0, 0), note, font=font_sm)[2:]
    draw.text(((w - nw) // 2, h - 48), note, fill=note_c, font=font_sm)

    im = _digest_apply_channel_watermark_rgb(im, channel_name)
    buf = io.BytesIO()
    im.save(buf, format='JPEG', quality=88, optimize=True)
    return buf.getvalue()


def download_digest_image_bytes(
    seed: str,
    *,
    channel_name: str = '',
    wx_ctx: dict[str, Any] | None = None,
) -> bytes | None:
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
            try:
                import io

                from PIL import Image

                bio = io.BytesIO(data)
                im = Image.open(bio)
                im.load()
                im = im.convert('RGB')
                im = _digest_apply_channel_watermark_rgb(im, channel_name)
                out = io.BytesIO()
                im.save(out, format='JPEG', quality=88, optimize=True)
                return out.getvalue()
            except Exception as exc:
                logger.warning('digest image watermark (picsum): %s', exc)
                return data
        logger.warning('digest image: picsum ответ слишком короткий (%s байт)', len(data))
    except Exception as exc:
        logger.warning('digest image (picsum): %s', exc)

    try:
        out = _digest_fallback_image_jpeg(seed, channel_name=channel_name, wx_ctx=wx_ctx)
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


def compose_digest_text(
    cfg, *, local_now: dt.datetime, day: dt.date, lat: float, lon: float
) -> tuple[str, str]:
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

    def _weather_detail_line(a: dict[str, Any]) -> str:
        return (
            f'{a.get("mm", 0)} мм рт.ст. · влажн. {a.get("hum", 0)}% · '
            f'{a.get("wms", 0)} м/с, {a.get("wdir", "—")}'
        )

    if cfg.block_date:
        block = f'🗓 {date_fmt} года'
        add_block(block, f'<b>{html_escape(block)}</b>')

    keys = get_global_api_keys()
    api_key = (keys.get_deepseek_api_key() or '').strip()

    if getattr(cfg, 'block_yesterday_news', True):
        yesterday = day - dt.timedelta(days=1)
        news_line = fetch_yesterday_channel_news_one_liner(
            channel=cfg.channel,
            yesterday=yesterday,
            tz_name=tz_name,
            api_key=api_key,
            use_ai=getattr(cfg, 'use_ai_yesterday_news', True),
        )
        head_news = '📰 Вчера в канале'
        add_block(
            f'{head_news}\n     {news_line}',
            f'<b>{html_escape(head_news)}</b>\n     {html_escape(news_line)}',
        )

    hourly = None
    if cfg.block_weather:
        hourly = fetch_open_meteo_day(lat, lon, tz_name, day)
        if hourly:
            agg = _aggregate_periods(hourly, tz_name)
            loc = (cfg.location_label or '').strip()
            head = '🌤 Погода на сегодня'
            if loc:
                head += f' ({loc})'
            plines = [head]
            hlines = [f'<b>{html_escape(head)}</b>']
            for key, cap, _low, period_emoji in PERIOD_LABELS:
                a = agg.get(key) or {}
                if not a:
                    plines.extend(
                        [
                            f'     {period_emoji} {cap}',
                            '        нет данных по прогнозу',
                        ]
                    )
                    hlines.extend(
                        [
                            f'     {period_emoji} <b>{html_escape(cap)}</b>',
                            f'        {html_escape("нет данных по прогнозу")}',
                        ]
                    )
                else:
                    t1, t2 = a['tmin'], a['tmax']
                    span = _digest_temp_span(t1, t2)
                    wx = a.get('wx', '—')
                    line1 = f'     {period_emoji} {cap}'
                    line2 = f'        {span} · {wx}'
                    line3 = f'        {_weather_detail_line(a)}'
                    plines.extend([line1, line2, line3])
                    hlines.extend(
                        [
                            f'     {period_emoji} <b>{html_escape(cap)}</b>',
                            f'        {html_escape(f"{span} · {wx}")}',
                            f'        {html_escape(_weather_detail_line(a))}',
                        ]
                    )
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

    want_ai = (
        (cfg.use_ai_quote and cfg.block_quote)
        or (cfg.use_ai_english and cfg.block_english)
        or (cfg.use_ai_horoscope and cfg.block_horoscope)
    )
    ai: dict[str, str] = {}
    if want_ai and api_key:
        try:
            ai = fetch_ai_blocks(date_str=date_fmt, api_key=api_key)
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
            sub = '     ✨ Праздников и памятных дат в календаре на сегодня нет'
            add_block(
                f'{head}:\n{sub}',
                f'<b>{html_escape(head)}:</b>\n{html_escape(sub)}',
            )

    if getattr(cfg, 'block_holidays_tomorrow', True):
        tmr = day + dt.timedelta(days=1)
        hol_t = format_holidays(cfg.country_for_holidays, tmr)
        head_t = '🎉 Праздники завтра'
        if hol_t:
            add_block(
                f'{head_t}:\n{hol_t}',
                f'<b>{html_escape(head_t)}:</b>\n' + _weather_line_html(hol_t),
            )
        else:
            sub_t = '     ✨ Праздников и памятных дат в календаре на завтра нет'
            add_block(
                f'{head_t}:\n{sub_t}',
                f'<b>{html_escape(head_t)}:</b>\n{html_escape(sub_t)}',
            )

    if cfg.block_horoscope:
        head = '✨ Гороскоп на сегодня'
        if cfg.use_ai_horoscope and api_key and (ai.get('horoscope_unified') or '').strip():
            ho = ai['horoscope_unified'].strip()
        else:
            ho = (fb.get('horoscope_unified') or fb.get('horoscope_ru') or '').strip()
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
        tz_img = cfg.timezone_name or 'Europe/Moscow'
        wx_ctx = _digest_image_weather_context(day, lat, lon, tz_img)
        seed = _digest_build_image_seed(
            day=day,
            channel_pk=channel.pk,
            image_seed_extra=cfg.image_seed_extra or '',
            local_now=local_now,
            wx_ctx=wx_ctx,
        )
        blob = download_digest_image_bytes(
            seed,
            channel_name=(channel.name or '').strip(),
            wx_ctx=wx_ctx,
        )
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
