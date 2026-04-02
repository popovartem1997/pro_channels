"""
Сервисный слой для рекламных заказов.
"""

from __future__ import annotations

from datetime import datetime, time

from django.db import transaction
from django.utils import timezone


@transaction.atomic
def ensure_ad_post_for_order(order) -> int:
    """
    Создаёт (если ещё не создан) Post для рекламного заказа и планирует публикацию.

    Правила MVP:
    - пост создаётся один раз (order.post)
    - текст = order.description
    - ord_label = "Реклама" (включает авто-ОРД регистрацию в content.tasks после публикации)
    - каналы = order.channels
    - scheduled_at:
        - если order.start_date <= сегодня → публикуем сразу через Celery
        - иначе планируем на start_date 10:00 (Europe/Moscow), через STATUS_SCHEDULED
    - повтор:
        - если order.repeat_interval_days > 0 → включаем повтор каждые N дней до end_date
    """
    from content.models import Post

    order = order.__class__.objects.select_for_update().prefetch_related("channels").get(pk=order.pk)
    if order.post_id:
        return order.post_id

    channels = list(order.channels.all())
    if not channels:
        raise ValueError("Для рекламного заказа не выбраны каналы.")

    # Автор поста — владелец каналов (для MVP считаем, что каналы принадлежат одному владельцу).
    author = channels[0].owner

    # Планируем публикацию на начало кампании
    now = timezone.now()
    start_dt = timezone.make_aware(datetime.combine(order.start_date, time(10, 0)))

    if start_dt <= now:
        status = Post.STATUS_DRAFT
        scheduled_at = None
    else:
        status = Post.STATUS_SCHEDULED
        scheduled_at = start_dt

    repeat_interval_days = int(getattr(order, "repeat_interval_days", 0) or 0)
    repeat_enabled = repeat_interval_days > 0

    post = Post.objects.create(
        author=author,
        text=order.description,
        status=status,
        scheduled_at=scheduled_at,
        ord_label="Реклама",
        repeat_enabled=repeat_enabled,
        repeat_type=(Post.REPEAT_INTERVAL if repeat_enabled else Post.REPEAT_NONE),
        repeat_interval_days=(repeat_interval_days or 3),
        repeat_end_date=order.end_date,
        disable_notification=False,
        pin_message=False,
    )
    post.channels.set(channels)

    order.post = post
    order.save(update_fields=["post"])

    # Если уже пора — публикуем сразу
    if start_dt <= now:
        from content.tasks import publish_post_task
        publish_post_task.delay(post.pk)

    return post.pk


def _safe_username(base: str) -> str:
    b = (base or '').strip().lower()
    b = ''.join(ch if (ch.isalnum() or ch in ('_', '-', '.')) else '_' for ch in b)
    return (b or 'ord_advertiser')[:150]


def sync_advertisers_and_contracts_from_ord(*, use_sandbox: bool) -> dict:
    """
    1) Подтягивает контрагентов (person) из кабинета ОРД в нашу базу как Advertiser + User (stub).
    2) Подтягивает договоры (contract) в advertisers.OrdContract и пытается сопоставить с Advertiser по person_external_id.
    """
    from django.contrib.auth import get_user_model
    from core.models import get_global_api_keys
    from ord_marking import vk_ord_client
    from .models import Advertiser, OrdContract

    keys = get_global_api_keys()
    bearer = (keys.get_vk_ord_access_token() or '').strip()
    if not bearer:
        return {'ok': False, 'error': 'Нет токена ОРД VK в «Ключи API».', 'created': 0, 'updated': 0, 'contracts': 0}

    User = get_user_model()
    created = 0
    updated = 0
    contracts = 0

    # --- persons ---
    person_ids = vk_ord_client.list_v1_entity_external_ids(bearer, 'person', limit=1000, use_sandbox=use_sandbox)
    for pid in person_ids:
        # Небольшая пауза, чтобы не упереться в 429
        try:
            import time as _time
            _time.sleep(0.12)
        except Exception:
            pass
        pdata = vk_ord_client.get_v1_entity_json(bearer, 'person', pid, use_sandbox=use_sandbox)
        name = (pdata.get('name') or '').strip() or pid
        jd = pdata.get('juridical_details') if isinstance(pdata, dict) else None
        inn = ''
        phone = ''
        if isinstance(jd, dict):
            inn = (jd.get('inn') or jd.get('foreign_inn') or '').strip()
            phone = (jd.get('phone') or '').strip()
        if not inn:
            # Без ИНН создаём только если точно нужен аккаунт: пока пропускаем, чтобы не плодить мусор.
            continue

        # Найдём существующего рекламодателя по ord_person_external_id или ИНН.
        adv = (
            Advertiser.objects.filter(ord_person_external_id=pid).first()
            or Advertiser.objects.filter(inn=inn).first()
        )
        if adv:
            changed = False
            if not adv.ord_person_external_id:
                adv.ord_person_external_id = pid
                changed = True
            if not adv.company_name and name:
                adv.company_name = name
                changed = True
            if changed:
                adv.save(update_fields=['ord_person_external_id', 'company_name'])
                updated += 1
        else:
            username = _safe_username(f'ord_{inn}_{pid}')
            if User.objects.filter(username=username).exists():
                username = _safe_username(f'ord_{pid}')
            user = User(username=username, first_name=name[:150], phone=phone, role=User.ROLE_ADVERTISER)
            user.set_unusable_password()
            user.save()
            adv = Advertiser.objects.create(
                user=user,
                company_name=name[:255] or f'Контрагент {inn}',
                inn=inn[:12],
                legal_address='—',
                contact_person=name[:255] or '—',
                contact_phone=phone[:20],
                ord_person_external_id=pid,
            )
            created += 1

    # --- contracts ---
    contract_ids = vk_ord_client.list_v1_entity_external_ids(bearer, 'contract', limit=1000, use_sandbox=use_sandbox)
    for cid in contract_ids:
        try:
            import time as _time
            _time.sleep(0.12)
        except Exception:
            pass
        cdata = vk_ord_client.get_v1_entity_json(bearer, 'contract', cid, use_sandbox=use_sandbox)
        if not isinstance(cdata, dict):
            continue
        client_pid = (cdata.get('client_external_id') or '').strip()
        contractor_pid = (cdata.get('contractor_external_id') or '').strip()
        adv = None
        if client_pid:
            adv = Advertiser.objects.filter(ord_person_external_id=client_pid).first()
        if not adv and contractor_pid:
            adv = Advertiser.objects.filter(ord_person_external_id=contractor_pid).first()

        obj, _ = OrdContract.objects.update_or_create(
            external_id=str(cid).strip(),
            defaults={
                'type': str(cdata.get('type') or ''),
                'client_external_id': client_pid,
                'contractor_external_id': contractor_pid,
                'date': str(cdata.get('date') or ''),
                'serial': str(cdata.get('serial') or ''),
                'raw': cdata,
                'advertiser': adv,
            },
        )
        contracts += 1

    return {'ok': True, 'created': created, 'updated': updated, 'contracts': contracts}

