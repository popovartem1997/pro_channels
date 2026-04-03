"""
Автосоздание/обновление контрагента (person) и договора (contract) в VK ОРД по данным профиля рекламодателя.

Стабильные внешние id: prochannels_adv_{advertiser_pk}, prochannels_contract_adv_{advertiser_pk}.
Договор создаётся только если в «Ключи API» задан person исполнителя (оператор площадки).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.utils import timezone

from advertisers.models import Advertiser, OrdContract

logger = logging.getLogger(__name__)

DEFAULT_CONTRACT_SUBJECT = (
    'Оказание услуг по размещению интернет-рекламы (сервис ProChannels)'
)

# PUT /v1/contract — обязателен type и др. поля (см. документацию ОРД).
# subject_type: distribution — распространение рекламы (площадка/исполнитель размещает рекламу заказчика).
# org_distribution — «организация распространения» (договор РС ↔ агентство), нам не подходит.
CONTRACT_TYPE_SERVICE = 'service'
CONTRACT_SUBJECT_TYPE_AD_DISTRIBUTION = 'distribution'


def _ord_contract_amount_string(keys: Any, campaign_total: Decimal | None) -> str:
    """
    Сумма для поля amount в PUT /v1/contract: из настроек админа — либо итог заявки, либо фикс.
    """
    use_real = bool(getattr(keys, 'vk_ord_contract_sum_from_campaign_total', False))
    fixed = getattr(keys, 'vk_ord_contract_amount_fixed', None)
    if fixed is None:
        fixed = Decimal('0')
    else:
        fixed = Decimal(fixed)
    total = campaign_total
    if total is not None:
        total = Decimal(total)
    if use_real and total is not None and total > 0:
        d = total
    else:
        d = fixed
    if d < 0:
        d = Decimal('0')
    q = d.quantize(Decimal('0.01'))
    return format(q, 'f')


def person_external_id_for_advertiser(adv: Advertiser) -> str:
    cur = (adv.ord_person_external_id or '').strip()
    if cur:
        return cur
    return f'prochannels_adv_{adv.pk}'


def contract_external_id_for_advertiser(adv: Advertiser) -> str:
    return f'prochannels_contract_adv_{adv.pk}'


def _ord_juridical_model_scheme(inn: str) -> str:
    """
    Обязательное поле juridical_details.type в JSON API ОРД (physical, juridical, ip, …).
    При пустом Advertiser.ord_model_scheme — эвристика по длине ИНН (12 по умолчанию ip).
    """
    n = len((inn or '').strip())
    if n == 12:
        return 'ip'
    if n == 10:
        return 'juridical'
    return 'juridical'


def build_person_put_body(adv: Advertiser) -> dict:
    """Тело PUT person в формате, совместимом с GET /v1/person/{id} в проекте."""
    inn = (adv.inn or '').strip()
    chosen = (getattr(adv, 'ord_model_scheme', None) or '').strip()
    scheme = chosen if chosen else _ord_juridical_model_scheme(inn)
    # В API ОРД ключ — «type», не «model_scheme» (см. PUT /v1/person в документации).
    jd: dict = {
        'type': scheme,
        'inn': inn,
    }
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


def ensure_advertiser_ord_profile(
    adv: Advertiser,
    *,
    use_sandbox: bool,
    campaign_total: Decimal | None = None,
) -> dict:
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
    serial = f'PC-{adv.pk}'[:120]
    amount_str = _ord_contract_amount_string(keys, campaign_total)
    cbody: dict = {
        'type': CONTRACT_TYPE_SERVICE,
        'client_external_id': pid,
        'contractor_external_id': operator_pid,
        'date': today,
        'serial': serial,
        'subject_type': CONTRACT_SUBJECT_TYPE_AD_DISTRIBUTION,
        'flags': ['vat_included', 'contractor_is_creatives_reporter'],
        'amount': amount_str,
    }
    try:
        vk_ord_client.put_contract_v1(bearer, cid, cbody, use_sandbox=use_sandbox)
    except vk_ord_client.OrdVkApiError as e1:
        try:
            vk_ord_client.put_contract_v1(bearer, cid, {'contract': cbody}, use_sandbox=use_sandbox)
        except vk_ord_client.OrdVkApiError as e2:
            out['contract_error'] = str(e2)
            logger.warning('ORD put contract failed adv=%s: %s (retry: %s)', adv.pk, e2, e1)
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
                'type': CONTRACT_TYPE_SERVICE,
                'client_external_id': pid,
                'contractor_external_id': operator_pid,
                'date': today,
                'serial': serial,
                'raw': cbody,
                'advertiser': adv,
            },
        )

    return out
