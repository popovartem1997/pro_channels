"""
Расчёты и слоты для нового потока заявок рекламодателя (AdApplication).
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from django.db import transaction
from django.utils import dateparse
from django.utils import timezone

from channels.models import Channel, ChannelAdAddon, ChannelAdVolumeDiscount

logger = logging.getLogger(__name__)


def applicable_volume_discount_percent(channel: Channel, slot_count: int) -> Decimal:
    if slot_count <= 0:
        return Decimal('0')
    tier = (
        ChannelAdVolumeDiscount.objects.filter(channel=channel, min_posts__lte=slot_count)
        .order_by('-min_posts')
        .first()
    )
    return tier.discount_percent if tier else Decimal('0')


def sum_addons_for_codes(
    channel: Channel,
    codes: list[str],
    *,
    pin_hours: int = 0,
) -> tuple[Decimal, int]:
    """
    Сумма доп. услуг и максимальная длительность «топа» в минутах (для паузы очереди).
    """
    if not codes:
        return Decimal('0'), 0
    uniq = list(dict.fromkeys(c.strip() for c in codes if (c or '').strip()))
    total = Decimal('0')
    top_minutes = 0
    pin_hours = max(0, int(pin_hours or 0))

    for code in uniq:
        row = ChannelAdAddon.objects.filter(
            channel=channel,
            code__iexact=code,
            is_active=True,
        ).first()
        if not row:
            continue
        kind = row.addon_kind or ChannelAdAddon.ADDON_KIND_CUSTOM

        if kind == ChannelAdAddon.ADDON_KIND_PIN_HOURLY:
            if pin_hours > 0:
                total += (row.price or Decimal('0')) * Decimal(pin_hours)
        elif kind == ChannelAdAddon.ADDON_KIND_TOP_BLOCK:
            total += row.price or Decimal('0')
            bh = int(row.block_hours or 0)
            if bh > 0:
                top_minutes = max(top_minutes, bh * 60)
            elif row.top_duration_minutes:
                top_minutes = max(top_minutes, int(row.top_duration_minutes))
        else:
            total += row.price or Decimal('0')
            if row.top_duration_minutes and str(code).lower().startswith('top'):
                top_minutes = max(top_minutes, int(row.top_duration_minutes))

    return total, top_minutes


def quote_application(
    channel: Channel,
    slot_count: int,
    addon_codes: list[str],
    *,
    pin_hours: int = 0,
) -> dict:
    """Предварительный расчёт без сохранения."""
    base = (channel.ad_price or Decimal('0')) * Decimal(slot_count)
    disc_p = applicable_volume_discount_percent(channel, slot_count)
    after_disc = base * (Decimal('100') - disc_p) / Decimal('100')
    addons, top_min = sum_addons_for_codes(channel, addon_codes, pin_hours=pin_hours)
    total = after_disc + addons
    return {
        'price_subtotal': base,
        'discount_percent': disc_p,
        'after_discount': after_disc,
        'addons_total': addons,
        'total_amount': total,
        'top_block_minutes': top_min,
    }


def _parse_hhmm(s: str) -> dt.time | None:
    s = (s or '').strip()
    if not s:
        return None
    t = dateparse.parse_time(s[:8])
    if t:
        return t
    try:
        parts = s.replace('.', ':').split(':')
        if len(parts) >= 2:
            return dt.time(int(parts[0]), int(parts[1]))
    except (ValueError, TypeError):
        return None
    return None


def ensure_ad_slots_for_channel(channel: Channel, *, days_ahead: int | None = None) -> int:
    """
    Создаёт строки AdvertisingSlot по channel.ad_slot_schedule_json на горизонте дней.
    Возвращает количество созданных новых слотов.
    """
    from advertisers.models import AdvertisingSlot

    schedule = channel.ad_slot_schedule_json or []
    if not isinstance(schedule, list) or not schedule:
        return 0
    horizon = days_ahead if days_ahead is not None else int(channel.ad_slot_horizon_days or 56)
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    created = 0
    with transaction.atomic():
        for day_offset in range(horizon):
            d = today + dt.timedelta(days=day_offset)
            weekday = d.weekday()  # пн=0
            for block in schedule:
                if not isinstance(block, dict):
                    continue
                if int(block.get('weekday', -1)) != weekday:
                    continue
                times = block.get('times') or []
                if not isinstance(times, list):
                    continue
                for hm in times:
                    if not isinstance(hm, str):
                        continue
                    t = _parse_hhmm(hm)
                    if not t:
                        continue
                    naive = dt.datetime.combine(d, t)
                    starts = timezone.make_aware(naive, tz) if tz else timezone.make_aware(naive)
                    _, was_created = AdvertisingSlot.objects.get_or_create(
                        channel=channel,
                        starts_at=starts,
                        defaults={'application': None},
                    )
                    if was_created:
                        created += 1
    return created


def save_pricing_to_application(
    app,
    *,
    slot_count: int,
    addon_codes: list[str],
    pin_hours: int | None = None,
) -> None:
    from advertisers.models import AdApplication

    ph = int(app.ad_pin_hours or 0) if pin_hours is None else max(0, int(pin_hours))
    q = quote_application(app.channel, slot_count, addon_codes, pin_hours=ph)
    AdApplication.objects.filter(pk=app.pk).update(
        price_subtotal=q['price_subtotal'],
        discount_percent=q['discount_percent'],
        addons_total=q['addons_total'],
        total_amount=q['total_amount'],
        addon_codes=list(addon_codes),
        ad_pin_hours=ph,
    )
    app.refresh_from_db()


def prefill_ad_application_ord_fields(app) -> dict:
    """
    Подставляет в заявку person/pad/contract из профиля рекламодателя, канала и OrdContract.
    Возвращает словарь с ключами, которые были записаны (для сообщений в UI).
    """
    from advertisers.models import AdApplication, OrdContract

    updates: dict = {}
    adv = app.advertiser
    ch = app.channel

    pid = (adv.ord_person_external_id or '').strip()
    if pid and app.ord_person_external_id != pid:
        updates['ord_person_external_id'] = pid

    pad = (getattr(ch, 'ord_pad_external_id', None) or '').strip()
    if pad and app.ord_pad_external_id != pad:
        updates['ord_pad_external_id'] = pad

    cid = (
        OrdContract.objects.filter(advertiser=adv)
        .order_by('-updated_at', '-pk')
        .values_list('external_id', flat=True)
        .first()
    )
    if cid and app.ord_contract_external_id != cid:
        updates['ord_contract_external_id'] = cid

    if updates:
        AdApplication.objects.filter(pk=app.pk).update(**updates)
        app.refresh_from_db()
    return updates


def want_pin_for_fulfillment(app) -> bool:
    """Нужно ли ставить закреп на первом слоте после оплаты."""
    for code in app.addon_codes or []:
        code_s = str(code).strip()
        if not code_s:
            continue
        row = ChannelAdAddon.objects.filter(
            channel=app.channel,
            code__iexact=code_s,
            is_active=True,
        ).first()
        if row:
            if row.addon_kind == ChannelAdAddon.ADDON_KIND_PIN_HOURLY:
                if (app.ad_pin_hours or 0) > 0:
                    return True
            elif row.addon_kind == ChannelAdAddon.ADDON_KIND_CUSTOM and code_s.lower() in (
                ChannelAdAddon.CODE_PIN,
                ChannelAdAddon.CODE_PIN_24H,
            ):
                return True
        elif code_s.lower() in (ChannelAdAddon.CODE_PIN, ChannelAdAddon.CODE_PIN_24H):
            return True
    return False


def book_slots_for_application(app, slot_ids: list[int]) -> None:
    """Привязывает свободные слоты к заявке; снимает привязку со слотов не из списка."""
    from advertisers.models import AdApplication, AdvertisingSlot

    ids = [int(x) for x in slot_ids if str(x).isdigit()]
    with transaction.atomic():
        app = AdApplication.objects.select_for_update().get(pk=app.pk)
        AdvertisingSlot.objects.filter(application=app).exclude(pk__in=ids).update(application=None)
        taken = AdvertisingSlot.objects.filter(
            pk__in=ids,
            channel=app.channel,
        ).select_for_update()
        for s in taken:
            if s.application_id and s.application_id != app.pk:
                raise ValueError(f'Слот #{s.pk} уже занят другой заявкой')
        AdvertisingSlot.objects.filter(pk__in=ids, channel=app.channel).update(application=app)
        AdApplication.objects.filter(pk=app.pk).update(selected_slot_ids=ids)


def ensure_draft_post_for_application(app):
    """Черновик поста (автор — владелец канала, для публикации через его токены)."""
    from content.models import Post
    from advertisers.models import AdApplication

    if app.post_id:
        return app.post
    owner = app.channel.owner
    post = Post.objects.create(
        author=owner,
        text='',
        text_html='',
        status=Post.STATUS_DRAFT,
        ord_label='Реклама',
    )
    post.channels.set([app.channel])
    AdApplication.objects.filter(pk=app.pk).update(post=post)
    app.refresh_from_db()
    return post


def clone_scheduled_post_for_slot(
    app,
    template,
    *,
    starts_at,
    is_first: bool,
    want_pin: bool,
    top_block_minutes: int,
):
    """Один запланированный пост по слоту."""
    from content.models import Post, PostMedia, normalize_post_media_orders
    from content.tasks import publish_post_task

    owner = app.channel.owner
    now = timezone.now()
    pin = bool(is_first and want_pin)
    top_min = int(top_block_minutes or 0) if is_first else 0
    if starts_at <= now:
        st = Post.STATUS_DRAFT
        sched = None
    else:
        st = Post.STATUS_SCHEDULED
        sched = starts_at
    post = Post.objects.create(
        author=owner,
        text=template.text,
        text_html=template.text_html or '',
        status=st,
        scheduled_at=sched,
        ord_label=(template.ord_label or '').strip() or 'Реклама',
        pin_message=pin,
        ad_top_block_minutes=top_min,
        campaign_application=app,
        disable_notification=False,
    )
    post.channels.set([app.channel])
    for m in template.media_files.all().order_by('order', 'pk'):
        PostMedia.objects.create(
            post=post,
            file=m.file,
            media_type=m.media_type,
            order=m.order,
        )
    normalize_post_media_orders(post)
    if starts_at <= now:
        pid = post.pk
        transaction.on_commit(lambda: publish_post_task.delay(pid))
    return post


def _draft_post_has_body(template) -> bool:
    """Черновик с текстом или с непустым HTML после снятия тегов."""
    if not template:
        return False
    if (template.text or '').strip():
        return True
    from django.utils.html import strip_tags

    return bool(strip_tags(template.text_html or '').strip())


@transaction.atomic
def fulfill_paid_ad_application(app) -> tuple[bool, str]:
    """
    После оплаты: создаёт посты по слотам. Идемпотентно.
    Возвращает (True, '') при успехе или если публикации уже есть;
    (False, 'причина') — если создать нельзя (текст для пользователя/лога).
    """
    from advertisers.models import AdApplication, AdvertisingSlot
    from content.models import Post

    app = AdApplication.objects.select_for_update().select_related('channel', 'post').get(pk=app.pk)
    if app.campaign_posts.exists():
        return True, ''
    template = app.post
    if not template:
        logger.warning('fulfill AdApplication #%s: нет привязанного черновика (post)', app.pk)
        return False, (
            'К заявке не привязан черновик поста. Пройдите мастер заявки до шага с текстом и сохраните материалы.'
        )
    if not _draft_post_has_body(template):
        logger.warning('fulfill AdApplication #%s: пустой черновик (text/html)', app.pk)
        return False, (
            'В черновике нет видимого текста: заполните поле текста или оформление в редакторе '
            '(пустой HTML или одни пробелы не подходят).'
        )
    ids = list(app.selected_slot_ids or [])
    if not ids:
        logger.warning('fulfill AdApplication #%s: нет слотов', app.pk)
        return False, (
            'В заявке не сохранены выбранные слоты размещения. Откройте шаг со слотами в мастере заявки '
            'и сохраните даты ещё раз до оплаты.'
        )
    slot_map = {
        s.pk: s
        for s in AdvertisingSlot.objects.filter(pk__in=ids, channel=app.channel, application_id=app.pk)
    }
    slots = [slot_map[i] for i in ids if i in slot_map]
    if len(slots) != len(ids):
        logger.warning(
            'fulfill AdApplication #%s: слоты не совпадают (ожидали %s, нашли %s)',
            app.pk,
            len(ids),
            len(slots),
        )
        return False, (
            f'Слоты в заявке не сходятся с базой: сохранено id слотов — {len(ids)}, '
            f'найдено под этим каналом и заявкой — {len(slots)}. '
            'Так бывает, если слоты пересоздавали или меняли канал: заново выберите слоты в мастере заявки.'
        )
    want_pin = want_pin_for_fulfillment(app)
    _, top_mins = sum_addons_for_codes(
        app.channel,
        app.addon_codes or [],
        pin_hours=app.ad_pin_hours or 0,
    )
    for i, slot in enumerate(slots):
        clone_scheduled_post_for_slot(
            app,
            template,
            starts_at=slot.starts_at,
            is_first=(i == 0),
            want_pin=want_pin,
            top_block_minutes=top_mins,
        )
    # Черновик скрываем из очереди публикации (остаётся в БД для истории)
    if template.status not in (Post.STATUS_PUBLISHED,):
        Post.objects.filter(pk=template.pk).update(status=Post.STATUS_DRAFT, scheduled_at=None)
    AdApplication.objects.filter(pk=app.pk).update(status=AdApplication.STATUS_SCHEDULED)
    return True, ''


def build_contract_html(app) -> str:
    from django.template.loader import render_to_string

    return render_to_string(
        'advertisers/contract_offer_body.html',
        {
            'app': app,
            'adv': app.advertiser,
            'ch': app.channel,
        },
    )
