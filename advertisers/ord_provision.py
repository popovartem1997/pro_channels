"""
Автосоздание/обновление контрагента (person) и договора (contract) в VK ОРД по данным профиля рекламодателя.

Стабильные внешние id: prochannels_adv_{advertiser_pk}, prochannels_contract_adv_{advertiser_pk}.
Договор создаётся только если в «Ключи API» задан person исполнителя (оператор площадки).
"""
from __future__ import annotations

import logging
from django.utils import timezone

from advertisers.models import Advertiser, OrdContract

logger = logging.getLogger(__name__)

DEFAULT_CONTRACT_SUBJECT = (
    'Оказание услуг по размещению интернет-рекламы (сервис ProChannels)'
)


def person_external_id_for_advertiser(adv: Advertiser) -> str:
    cur = (adv.ord_person_external_id or '').strip()
    if cur:
        return cur
    return f'prochannels_adv_{adv.pk}'


def contract_external_id_for_advertiser(adv: Advertiser) -> str:
    return f'prochannels_contract_adv_{adv.pk}'


def build_person_put_body(adv: Advertiser) -> dict:
    """Тело PUT person в формате, совместимом с GET /v1/person/{id} в проекте."""
    inn = (adv.inn or '').strip()
    jd: dict = {'inn': inn}
    if len(inn) == 10 and (adv.kpp or '').strip():
        jd['kpp'] = (adv.kpp or '').strip()[:9]
    if (adv.ogrn or '').strip():
        jd['ogrn'] = (adv.ogrn or '').strip()[:15]
    phone = (adv.contact_phone or '').strip()
    if phone:
        jd['phone'] = phone[:32]
    addr = (adv.legal_address or '').strip()
    if addr:
        jd['legal_address'] = addr[:1000]

    name = (adv.company_name or '').strip() or f'Рекламодатель ИНН {inn}'
    return {
        'name': name[:500],
        'roles': ['advertiser'],
        'juridical_details': jd,
    }


def ensure_advertiser_ord_profile(adv: Advertiser, *, use_sandbox: bool) -> dict:
    """
    PUT person (и при наличии настроек — PUT contract) в ОРД.
    Сохраняет ord_person_external_id у рекламодателя и зеркалит договор в OrdContract.
    """
    from core.models import get_global_api_keys
    from ord_marking import vk_ord_client

    keys = get_global_api_keys()
    bearer = (keys.get_vk_ord_access_token() or '').strip()
    out: dict = {'ok': False, 'person_id': '', 'contract_id': '', 'error': ''}

    if not bearer:
        out['error'] = 'Не задан Bearer-токен ОРД VK в «Ключи API».'
        return out

    pid = person_external_id_for_advertiser(adv)
    body = build_person_put_body(adv)

    try:
        vk_ord_client.put_person_v1(bearer, pid, body, use_sandbox=use_sandbox)
    except vk_ord_client.OrdVkApiError as e1:
        # Часть окружений ожидает тело {"person": {...}} (как в официальном SDK).
        try:
            vk_ord_client.put_person_v1(bearer, pid, {'person': body}, use_sandbox=use_sandbox)
        except vk_ord_client.OrdVkApiError as e2:
            out['error'] = str(e2)
            logger.warning('ORD put person failed adv=%s: %s (retry: %s)', adv.pk, e2, e1)
            return out
    except Exception as e:
        out['error'] = str(e)[:2000]
        logger.exception('ORD put person adv=%s', adv.pk)
        return out

    if adv.ord_person_external_id != pid:
        Advertiser.objects.filter(pk=adv.pk).update(ord_person_external_id=pid)
        adv.ord_person_external_id = pid

    out['ok'] = True
    out['person_id'] = pid

    operator_pid = (getattr(keys, 'vk_ord_operator_person_external_id', None) or '').strip()
    if not operator_pid:
        return out

    cid = contract_external_id_for_advertiser(adv)
    today = timezone.now().date().isoformat()
    cbody: dict = {
        'client_external_id': pid,
        'contractor_external_id': operator_pid,
        'subject': DEFAULT_CONTRACT_SUBJECT[:2000],
        'date': today,
    }
    try:
        vk_ord_client.put_contract_v1(bearer, cid, cbody, use_sandbox=use_sandbox)
    except vk_ord_client.OrdVkApiError as e:
        out['contract_error'] = str(e)
        logger.warning('ORD put contract failed adv=%s: %s', adv.pk, e)
        return out
    except Exception as e:
        out['contract_error'] = str(e)[:2000]
        logger.exception('ORD put contract adv=%s', adv.pk)
        return out

    out['contract_id'] = cid
    try:
        raw = vk_ord_client.get_v1_entity_json(bearer, 'contract', cid, use_sandbox=use_sandbox)
        client_pid = (raw.get('client_external_id') or '').strip()
        contractor_pid = (raw.get('contractor_external_id') or '').strip()
        OrdContract.objects.update_or_create(
            external_id=str(cid).strip(),
            defaults={
                'type': str(raw.get('type') or ''),
                'client_external_id': client_pid,
                'contractor_external_id': contractor_pid,
                'date': str(raw.get('date') or today),
                'serial': str(raw.get('serial') or ''),
                'raw': raw if isinstance(raw, dict) else {},
                'advertiser': adv,
            },
        )
    except Exception as e:
        logger.warning('ORD mirror OrdContract adv=%s: %s', adv.pk, e)
        OrdContract.objects.update_or_create(
            external_id=str(cid).strip(),
            defaults={
                'type': '',
                'client_external_id': pid,
                'contractor_external_id': operator_pid,
                'date': today,
                'serial': '',
                'raw': cbody,
                'advertiser': adv,
            },
        )

    return out
