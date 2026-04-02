"""
Бизнес-логика маркировки ОРД VK: креатив, erid, статистика.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time

from django.utils import timezone

from . import vk_ord_client
from .models import ORDRegistration


def load_ord_catalog(bearer: str, *, use_sandbox: bool) -> dict:
    """
    Списки внешних id из кабинета ОРД (те же данные, что в ЛК ord.vk.com), по API.
    """
    out: dict = {'person_ids': [], 'contract_ids': [], 'pad_ids': [], 'catalog_error': None}
    if not (bearer or '').strip():
        return out
    try:
        out['person_ids'] = vk_ord_client.list_v1_entity_external_ids(
            bearer, 'person', limit=400, use_sandbox=use_sandbox
        )
        out['contract_ids'] = vk_ord_client.list_v1_entity_external_ids(
            bearer, 'contract', limit=400, use_sandbox=use_sandbox
        )
        out['pad_ids'] = vk_ord_client.list_v1_entity_external_ids(
            bearer, 'pad', limit=400, use_sandbox=use_sandbox
        )
    except vk_ord_client.OrdVkApiError as e:
        if getattr(e, 'status', None) == 401:
            out['catalog_error'] = (
                '401 Unauthorized. Проверьте токен ОРД VK в «Ключи API» '
                '(вставляйте только сам токен, без слова "Bearer") '
                'и режим песочницы (sandbox должен соответствовать месту, где вы создавали токен).'
            )
        else:
            out['catalog_error'] = str(e)
    except Exception as e:
        out['catalog_error'] = str(e)
    return out


def creative_external_id_for(post_id: int, channel_id: int) -> str:
    return f'pc-p{post_id}-c{channel_id}'


def build_creative_body(
    *,
    post_text: str,
    channel_name: str,
    advertiser_company: str | None,
    contract_external_id: str,
    person_external_id: str,
    target_urls: list[str],
) -> dict:
    """Тело PUT /v2/creative/{id} (упрощённый пост/текст)."""
    text = (post_text or '').strip() or 'Рекламный пост'
    brand = (advertiser_company or channel_name or 'Реклама')[:200]
    body: dict = {
        'okveds': ['73.11'],
        'kktus': ['1.2.1'],
        'name': text[:200],
        'brand': brand,
        'category': 'Реклама в социальных сетях',
        'description': text[:2000],
        'pay_type': 'other',
        'form': 'text_graphic_block',
        'texts': [text[:3500]],
        'target_urls': target_urls[:20] if target_urls else ['https://vk.com'],
    }
    pe = (person_external_id or '').strip()
    ce = (contract_external_id or '').strip()
    if pe:
        body['person_external_id'] = pe
    elif ce:
        body['contract_external_id'] = ce
    return body


def _vk_channel_url(channel) -> str:
    gid = (getattr(channel, 'vk_group_id', None) or '').strip()
    if gid:
        return f'https://vk.com/club{gid.lstrip("-")}'
    return 'https://vk.com'


def register_creative_for_registration(reg: ORDRegistration, *, use_sandbox: bool) -> ORDRegistration:
    """Вызывает ОРД API и обновляет reg + синхронизирует post.ord_token."""
    from core.models import get_global_api_keys
    from content.models import Post

    keys = get_global_api_keys()
    bearer = (keys.get_vk_ord_access_token() or '').strip()
    if not bearer:
        reg.status = ORDRegistration.STATUS_ERROR
        reg.error_message = 'В «Ключи API» не задан ключ ОРД VK (Bearer из кабинета ord.vk.com).'
        reg.save()
        return reg

    contract = (
        (reg.contract_external_id or '').strip()
        or (keys.vk_ord_contract_external_id or '').strip()
    )
    person = (reg.person_external_id or '').strip()
    if reg.advertiser_id:
        from advertisers.models import Advertiser

        try:
            adv = Advertiser.objects.get(pk=reg.advertiser_id)
            person = person or (adv.ord_person_external_id or '').strip()
        except Advertiser.DoesNotExist:
            pass

    if not person and not contract:
        reg.status = ORDRegistration.STATUS_ERROR
        reg.error_message = (
            'Укажите договор ОРД (в ключах API или в форме) либо внешний ID контрагента '
            '(в карточке рекламодателя или в форме).'
        )
        reg.save()
        return reg

    post = reg.post
    channel = reg.channel
    ext_id = creative_external_id_for(post.pk, channel.pk)
    reg.creative_external_id = ext_id

    targets = []
    if channel.platform == channel.PLATFORM_VK:
        targets.append(_vk_channel_url(channel))
    from django.conf import settings

    site = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
    if site:
        targets.append(site + '/posts/' + str(post.pk) + '/')

    creative_text = (post.text or '').strip() or 'Рекламный пост'
    try:
        media_urls: list[str] = []
        for m in post.media_files.all():
            fn = getattr(getattr(m, 'file', None), 'name', None) or ''
            if not fn:
                continue
            u = m.file.url
            if u.startswith('http'):
                media_urls.append(u[:500])
            elif site:
                media_urls.append((site.rstrip('/') + u)[:500])
        if media_urls:
            creative_text = (
                creative_text + '\n\n[Медиа: ' + ', '.join(media_urls[:10]) + ']'
            )[:12000]
    except Exception:
        pass

    body = build_creative_body(
        post_text=creative_text,
        channel_name=channel.name,
        advertiser_company=reg.advertiser.company_name if reg.advertiser_id else None,
        contract_external_id=contract,
        person_external_id=person,
        target_urls=targets or ['https://vk.com'],
    )

    try:
        data = vk_ord_client.put_creative_v2(bearer, ext_id, body, use_sandbox=use_sandbox)
        reg.raw_response = data
        marker = (data.get('erid') or data.get('marker') or '').strip()
        reg.erid = marker
        reg.ord_token = marker
        reg.ord_id = ext_id
        reg.status = ORDRegistration.STATUS_REGISTERED
        reg.registered_at = timezone.now()
        reg.error_message = ''
        reg.save()
        Post.objects.filter(pk=post.pk).update(ord_token=marker, ord_label=reg.label_text or 'Реклама')
    except vk_ord_client.OrdVkApiError as e:
        reg.status = ORDRegistration.STATUS_ERROR
        reg.error_message = str(e)[:2000]
        reg.raw_response = e.parsed if isinstance(e.parsed, dict) else {'error': str(e)}
        reg.save()
    except Exception as e:
        reg.status = ORDRegistration.STATUS_ERROR
        reg.error_message = str(e)[:2000]
        reg.save()
    return reg


def refresh_erid_from_api(reg: ORDRegistration, *, use_sandbox: bool) -> ORDRegistration:
    from core.models import get_global_api_keys
    from content.models import Post

    keys = get_global_api_keys()
    bearer = (keys.get_vk_ord_access_token() or '').strip()
    if not bearer:
        return reg
    ext = (reg.creative_external_id or '').strip() or creative_external_id_for(reg.post_id, reg.channel_id)
    try:
        pairs = vk_ord_client.fetch_erid_map(bearer, use_sandbox=use_sandbox, limit=1000)
        for it in pairs:
            if str(it.get('external_id')) == ext:
                er = (it.get('erid') or '').strip()
                if er:
                    reg.erid = er
                    reg.ord_token = er
                    reg.save(update_fields=['erid', 'ord_token'])
                    Post.objects.filter(pk=reg.post_id).update(ord_token=er)
                break
    except Exception:
        pass
    return reg


def submit_statistics_for_month(
    reg: ORDRegistration,
    year: int,
    month: int,
    *,
    use_sandbox: bool,
) -> tuple[bool, str]:
    """
    Отправить агрегированные показы за календарный месяц (из PostStat).
    """
    from core.models import get_global_api_keys
    from stats.models import PostStat

    keys = get_global_api_keys()
    bearer = (keys.get_vk_ord_access_token() or '').strip()
    if not bearer:
        return False, 'Нет ключа API ОРД'

    pad = (
        (reg.pad_external_id or '').strip()
        or (reg.channel.ord_pad_external_id or '').strip()
        or (keys.vk_ord_pad_external_id or '').strip()
    )
    if not pad:
        return False, 'Не задан внешний ID площадки (pad): в канале, в форме регистрации или в ключах API.'

    ext = (reg.creative_external_id or '').strip() or creative_external_id_for(reg.post_id, reg.channel_id)

    from stats.models import PostStat, PostStatSnapshot

    _, last_day = calendar.monthrange(year, month)
    start = date(year, month, 1)
    end = date(year, month, last_day)
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end, time(23, 59, 59)), tz)

    snaps = list(
        PostStatSnapshot.objects.filter(
            post_id=reg.post_id,
            channel_id=reg.channel_id,
            recorded_at__gte=start_dt,
            recorded_at__lte=end_dt,
        ).order_by('recorded_at')
    )
    if len(snaps) >= 2:
        total_views = max(0, int(snaps[-1].views or 0) - int(snaps[0].views or 0))
    elif len(snaps) == 1:
        total_views = max(0, int(snaps[0].views or 0))
    else:
        ps = PostStat.objects.filter(post_id=reg.post_id, channel_id=reg.channel_id).first()
        total_views = max(0, int(ps.views or 0) if ps else 0)

    item = {
        'creative_external_id': ext,
        'pad_external_id': pad,
        'shows_count': max(0, total_views),
        'date_start_actual': start.isoformat(),
        'date_end_actual': end.isoformat(),
    }

    try:
        vk_ord_client.post_statistics_v1(bearer, [item], use_sandbox=use_sandbox)
        reg.stats_submitted_at = timezone.now()
        reg.stats_error_message = ''
        reg.stats_raw_response = {'ok': True, 'year': year, 'month': month, 'shows': total_views}
        reg.save(update_fields=['stats_submitted_at', 'stats_error_message', 'stats_raw_response'])
        return True, f'Отправлено показов: {total_views} за {month:02d}.{year}'
    except vk_ord_client.OrdVkApiError as e:
        reg.stats_error_message = str(e)[:2000]
        reg.stats_raw_response = e.parsed if isinstance(e.parsed, dict) else {}
        reg.save(update_fields=['stats_error_message', 'stats_raw_response'])
        return False, str(e)
    except Exception as e:
        reg.stats_error_message = str(e)[:2000]
        reg.save(update_fields=['stats_error_message'])
        return False, str(e)
