"""
Мастер заявки на рекламу: канал → слоты → контент → ОРД → проверка → договор → оплата.
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing.models import Invoice
from channels.models import Channel, ChannelAdAddon

from .ad_campaign_services import (
    book_slots_for_application,
    build_contract_html,
    ensure_ad_slots_for_channel,
    ensure_draft_post_for_application,
    fulfill_paid_ad_application,
    prefill_ad_application_ord_fields,
    save_pricing_to_application,
)
from .models import AdApplication, Advertiser

logger = logging.getLogger(__name__)


def _iter_active_addons(channel: Channel):
    """
    Только две фиксированные опции из настроек канала: top_1h и pin_24h.
    """
    from django.db.models import Q

    rows = list(
        ChannelAdAddon.objects.filter(
            channel=channel,
            is_active=True,
        ).filter(Q(code__iexact='top_1h') | Q(code__iexact='pin_24h'))
    )
    by_lower = {(r.code or '').lower(): r for r in rows}
    top_addons = [by_lower['top_1h']] if 'top_1h' in by_lower else []
    pin_24_addons = [by_lower['pin_24h']] if 'pin_24h' in by_lower else []
    pin_addon = None
    custom_addons = []
    return pin_addon, top_addons, custom_addons, pin_24_addons


def _pin_hours_default(pin_addon: ChannelAdAddon | None) -> int:
    if not pin_addon:
        return 1
    max_h = int(pin_addon.max_pin_hours or 72)
    return min(24, max(1, max_h))


def _parse_addon_post(request, pin_addon: ChannelAdAddon | None):
    codes = list(request.POST.getlist('addon_codes'))
    pin_hours = 0
    if pin_addon:
        pcode = (pin_addon.code or '').strip()
        pin_checked = pcode in codes
        if not pin_checked:
            codes = [c for c in codes if str(c) != pcode]
            pin_hours = 0
        else:
            raw = (request.POST.get('ad_pin_hours') or '0').strip()
            pin_hours = int(raw) if raw.isdigit() else 0
            max_h = int(pin_addon.max_pin_hours or 72)
            pin_hours = min(max(0, pin_hours), max_h)
            if pin_hours < 1:
                return (
                    codes,
                    pin_hours,
                    'Для закрепа укажите число часов не меньше 1 '
                    f'(максимум {max_h}).',
                )
    return codes, pin_hours, None


def _addon_display_rows(app: AdApplication) -> list[dict]:
    """Строки для сводки: что выбрано из доп. услуг."""
    rows: list[dict] = []
    for code in app.addon_codes or []:
        code_s = str(code).strip()
        if not code_s:
            continue
        row = ChannelAdAddon.objects.filter(
            channel=app.channel, code__iexact=code_s, is_active=True
        ).first()
        if not row:
            rows.append({'label': code_s, 'detail': ''})
            continue
        kind = row.addon_kind or ChannelAdAddon.ADDON_KIND_CUSTOM
        if kind == ChannelAdAddon.ADDON_KIND_PIN_HOURLY:
            ph = int(app.ad_pin_hours or 0)
            rows.append(
                {
                    'label': row.title,
                    'detail': f'{ph} ч. × {row.price} ₽/ч = {(row.price or 0) * ph} ₽',
                }
            )
        elif kind == ChannelAdAddon.ADDON_KIND_TOP_BLOCK:
            bh = row.block_hours or ((row.top_duration_minutes or 0) // 60 if row.top_duration_minutes else 0)
            extra = f', без других постов {bh} ч.' if bh else ''
            rows.append({'label': row.title, 'detail': f'{row.price} ₽{extra}'})
        else:
            rows.append({'label': row.title, 'detail': f'{row.price} ₽'})
    return rows


def _advertiser(request):
    return get_object_or_404(Advertiser, user=request.user)


def _app_adv(request, pk: int) -> AdApplication:
    adv = _advertiser(request)
    return get_object_or_404(AdApplication, pk=pk, advertiser=adv)


@login_required
@require_POST
def campaign_delete(request, pk: int):
    """Удаление черновика заявки рекламодателем."""
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        messages.error(request, 'Удалить можно только заявку в статусе «Черновик».')
        return redirect('advertisers:campaign_list')
    channel_name = app.channel.name
    num = app.pk
    app.delete()
    messages.success(request, f'Заявка №{num} («{channel_name}») удалена.')
    return redirect('advertisers:campaign_list')


def _owner_allowed_channels_queryset(request):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    owner_ids = list(User.objects.filter(role=User.ROLE_OWNER).values_list('id', flat=True))
    return Channel.objects.filter(is_active=True, owner_id__in=owner_ids, ad_enabled=True)


@login_required
def campaign_resume(request, pk: int):
    """Продолжить черновик с первого незаполненного шага."""
    app = _app_adv(request, pk)
    if app.status == AdApplication.STATUS_PENDING_OWNER:
        return redirect('advertisers:campaign_pending_owner', pk=pk)
    if app.status == AdApplication.STATUS_APPROVED_FOR_PAYMENT:
        return redirect('advertisers:campaign_checkout', pk=pk)
    if app.status != AdApplication.STATUS_DRAFT:
        messages.info(request, 'Эта заявка уже отправлена или оплачена.')
        return redirect('advertisers:campaign_list')
    if not app.selected_slot_ids:
        return redirect('advertisers:campaign_slots', pk=pk)
    post = app.post
    if not post or not (post.text or '').strip():
        return redirect('advertisers:campaign_content', pk=pk)
    if not app.ord_wizard_saved_at:
        return redirect('advertisers:campaign_ord', pk=pk)
    return redirect('advertisers:campaign_review', pk=pk)


@login_required
def campaign_new(request):
    from urllib.parse import urlencode

    from django.urls import reverse

    try:
        adv = Advertiser.objects.get(user=request.user)
    except Advertiser.DoesNotExist:
        messages.info(request, 'Сначала заполните профиль рекламодателя.')
        next_path = request.get_full_path()
        if not (next_path or '').startswith('/'):
            next_path = reverse('advertisers:campaign_new')
        return redirect(f'{reverse("advertisers:register")}?{urlencode({"next": next_path})}')

    channels = _owner_allowed_channels_queryset(request).order_by('name')
    preselect_channel_id = None
    raw_get = (request.GET.get('channel') or request.GET.get('channel_id') or '').strip()
    if raw_get.isdigit() and channels.filter(pk=int(raw_get)).exists():
        preselect_channel_id = int(raw_get)

    if request.method == 'POST':
        raw = (request.POST.get('channel_id') or '').strip()
        form_preselect = (
            int(raw)
            if raw.isdigit() and channels.filter(pk=int(raw)).exists()
            else preselect_channel_id
        )
        if not raw.isdigit():
            messages.error(request, 'Выберите канал.')
            return render(
                request,
                'advertisers/campaign_channel.html',
                {'channels': channels, 'preselect_channel_id': form_preselect},
            )
        ch = get_object_or_404(channels, pk=int(raw))
        app = AdApplication.objects.create(
            advertiser=adv,
            channel=ch,
            status=AdApplication.STATUS_DRAFT,
        )
        return redirect('advertisers:campaign_slots', pk=app.pk)

    return render(
        request,
        'advertisers/campaign_channel.html',
        {'channels': channels, 'preselect_channel_id': preselect_channel_id},
    )


@login_required
def campaign_draft_channel(request, pk: int):
    """Шаг 1 мастера после создания черновика: канал уже выбран, можно вернуться сюда из навигации."""
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        if app.status == AdApplication.STATUS_PENDING_OWNER:
            return redirect('advertisers:campaign_pending_owner', pk=pk)
        if app.status == AdApplication.STATUS_APPROVED_FOR_PAYMENT:
            return redirect('advertisers:campaign_checkout', pk=pk)
        if app.status == AdApplication.STATUS_AWAITING_PAYMENT:
            return redirect('advertisers:campaign_checkout', pk=pk)
        messages.info(request, 'Эта заявка уже не в черновике.')
        return redirect('advertisers:campaign_list')
    return render(request, 'advertisers/campaign_draft_channel.html', {'app': app})


@login_required
def campaign_slots(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        messages.info(request, 'Эта заявка уже не в черновике.')
        return redirect('advertisers:campaign_list')

    ensure_ad_slots_for_channel(app.channel)
    from advertisers.models import AdvertisingSlot

    pin_addon, top_addons, custom_addons, pin_24_addons = _iter_active_addons(app.channel)
    pin_hours_default = _pin_hours_default(pin_addon)

    if request.method == 'POST':
        sel = request.POST.getlist('slot_ids')
        try:
            book_slots_for_application(app, [int(x) for x in sel if str(x).isdigit()])
            app.refresh_from_db()
            n = len(app.selected_slot_ids or [])
            if n < 1:
                messages.error(request, 'Выберите хотя бы один слот.')
            else:
                codes, pin_hours, ad_err = _parse_addon_post(request, pin_addon)
                if ad_err:
                    messages.error(request, ad_err)
                else:
                    save_pricing_to_application(
                        app,
                        slot_count=n,
                        addon_codes=codes,
                        pin_hours=pin_hours,
                    )
                    messages.success(request, f'Выбрано слотов: {n}.')
                    return redirect('advertisers:campaign_content', pk=app.pk)
        except ValueError as e:
            messages.error(request, str(e))

    slots = (
        AdvertisingSlot.objects.filter(channel=app.channel, starts_at__gte=timezone.now())
        .order_by('starts_at')[:500]
    )
    selected = set(int(x) for x in (app.selected_slot_ids or []) if str(x).isdigit())
    return render(
        request,
        'advertisers/campaign_slots.html',
        {
            'app': app,
            'slots': slots,
            'selected': selected,
            'pin_addon': pin_addon,
            'top_addons': top_addons,
            'custom_addons': custom_addons,
            'pin_24_addons': pin_24_addons,
            'pin_hours_default': pin_hours_default,
        },
    )


@login_required
def campaign_content(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        return redirect('advertisers:campaign_list')

    from content.models import PostMedia, normalize_post_media_orders

    post = ensure_draft_post_for_application(app)
    if request.method == 'POST':
        post.text = (request.POST.get('text') or '').strip()
        post.text_html = (request.POST.get('text_html') or '').strip()
        post.ord_label = (request.POST.get('ord_label') or 'Реклама').strip() or 'Реклама'
        if not post.text:
            messages.error(request, 'Введите текст поста.')
            return render(request, 'advertisers/campaign_content.html', {'app': app, 'post': post})

        keep_media_ids = request.POST.getlist('keep_media')
        if keep_media_ids:
            keep_set = {int(x) for x in keep_media_ids if str(x).isdigit()}
            to_delete = PostMedia.objects.filter(post=post).exclude(pk__in=keep_set)
            for m in to_delete:
                try:
                    m.file.delete(save=False)
                except Exception:
                    pass
            to_delete.delete()

        for m in PostMedia.objects.filter(post=post):
            key = f'media_order_{m.pk}'
            if key in request.POST:
                raw = (request.POST.get(key) or '').strip()
                if raw.isdigit():
                    m.order = max(1, int(raw))
                    m.save(update_fields=['order'])

        max_order = PostMedia.objects.filter(post=post).aggregate(m=Max('order'))['m']
        base_order = int(max_order) if max_order is not None else 0
        for idx, f in enumerate(request.FILES.getlist('media_files')):
            media_type = PostMedia.TYPE_PHOTO
            if f.content_type.startswith('video'):
                media_type = PostMedia.TYPE_VIDEO
            elif not f.content_type.startswith('image'):
                media_type = PostMedia.TYPE_DOCUMENT
            PostMedia.objects.create(post=post, file=f, media_type=media_type, order=base_order + idx + 1)

        normalize_post_media_orders(post)
        post.save()
        messages.success(request, 'Материалы сохранены.')
        return redirect('advertisers:campaign_ord', pk=app.pk)

    return render(request, 'advertisers/campaign_content.html', {'app': app, 'post': post})


def _run_campaign_ord_prepare(app: AdApplication, *, sync_catalog: bool, use_sandbox: bool) -> dict:
    """
    Синхронизация каталога (опционально), ensure person/contract в ОРД, prefill полей заявки,
    отметка шага мастера. Возвращает словарь для JSON-ответа UI.
    """
    from core.models import get_global_api_keys

    from advertisers.ord_provision import ensure_advertiser_ord_profile
    from advertisers.services import sync_advertisers_and_contracts_from_ord

    out: dict = {
        'ok': False,
        'person_ok': False,
        'contract_id': '',
        'contract_skipped': False,
        'person_error': '',
        'contract_error': '',
        'sync': None,
    }

    if sync_catalog:
        sr = sync_advertisers_and_contracts_from_ord(use_sandbox=use_sandbox)
        out['sync'] = {
            'ok': bool(sr.get('ok')),
            'created': sr.get('created', 0),
            'updated': sr.get('updated', 0),
            'contracts': sr.get('contracts', 0),
            'error': (sr.get('error') or '').strip(),
        }
        app.refresh_from_db()
        app.advertiser.refresh_from_db()
        app.channel.refresh_from_db()

    prov = ensure_advertiser_ord_profile(
        app.advertiser,
        use_sandbox=use_sandbox,
        campaign_total=app.total_amount,
    )
    app.advertiser.refresh_from_db()

    if not prov.get('ok'):
        out['person_error'] = (prov.get('error') or 'Не удалось создать контрагента в ОРД.').strip()
        return out

    out['person_ok'] = True
    out['contract_id'] = (prov.get('contract_id') or '').strip()
    out['contract_error'] = (prov.get('contract_error') or '').strip()
    keys = get_global_api_keys()
    operator_set = bool((getattr(keys, 'vk_ord_operator_person_external_id', None) or '').strip())
    out['contract_skipped'] = operator_set is False

    prefill_ad_application_ord_fields(app)
    app.refresh_from_db()

    now = timezone.now()
    app.ord_sync_error = ''
    if app.ord_contract_external_id or app.ord_person_external_id or app.ord_pad_external_id:
        app.ord_synced_at = now
    app.ord_wizard_saved_at = now
    app.save(
        update_fields=[
            'ord_contract_external_id',
            'ord_person_external_id',
            'ord_pad_external_id',
            'ord_synced_at',
            'ord_sync_error',
            'ord_wizard_saved_at',
            'updated_at',
        ]
    )

    out['ok'] = True
    return out


@login_required
@require_POST
def campaign_ord_prepare(request, pk: int):
    """AJAX: подготовка ОРД (person/contract + prefill). mode=provision|sync"""
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        return JsonResponse({'ok': False, 'error': 'Заявка не в черновике.'}, status=400)

    from core.models import get_global_api_keys

    keys = get_global_api_keys()
    sandbox = bool(getattr(keys, 'vk_ord_use_sandbox', False))
    mode = (request.POST.get('mode') or 'provision').strip()
    sync_catalog = mode == 'sync'

    try:
        result = _run_campaign_ord_prepare(app, sync_catalog=sync_catalog, use_sandbox=sandbox)
    except Exception as e:
        logger.exception('campaign_ord_prepare app=%s', pk)
        return JsonResponse({'ok': False, 'error': str(e)[:2000]}, status=500)

    if not result.get('ok'):
        err = result.get('person_error') or 'Ошибка подготовки ОРД.'
        return JsonResponse({**result, 'error': err}, status=200)

    warnings = []
    if result.get('sync') and not result['sync'].get('ok') and result['sync'].get('error'):
        warnings.append(f'Каталог ОРД: {result["sync"]["error"][:400]}')
    if result.get('contract_error'):
        warnings.append(f'Договор в ОРД: {result["contract_error"][:400]}')

    return JsonResponse(
        {
            **result,
            'warnings': warnings,
            'error': '',
        }
    )


@login_required
def campaign_ord(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        return redirect('advertisers:campaign_list')

    if request.method == 'POST':
        action = (request.POST.get('action') or 'next').strip()
        if action != 'next':
            return redirect('advertisers:campaign_ord', pk=app.pk)
        app.refresh_from_db()
        if not app.ord_wizard_saved_at:
            messages.error(
                request,
                'Сначала дождитесь окончания подготовки данных ВК ОРД на этой странице.',
            )
            return redirect('advertisers:campaign_ord', pk=app.pk)
        messages.success(request, 'Шаг ВК ОРД пройден. Далее — сводка заявки.')
        return redirect('advertisers:campaign_review', pk=app.pk)

    app.advertiser.refresh_from_db()
    app.channel.refresh_from_db()
    return render(request, 'advertisers/campaign_ord.html', {'app': app})


@login_required
def campaign_review(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        return redirect('advertisers:campaign_list')

    n = len(app.selected_slot_ids or [])
    if n:
        save_pricing_to_application(app, slot_count=n, addon_codes=app.addon_codes or [])
        app.refresh_from_db()

    addon_rows = _addon_display_rows(app)

    return render(
        request,
        'advertisers/campaign_review.html',
        {
            'app': app,
            'addon_rows': addon_rows,
        },
    )


@login_required
def campaign_contract(request, pk: int):
    app = _app_adv(request, pk)
    if app.status == AdApplication.STATUS_PENDING_OWNER:
        return redirect('advertisers:campaign_pending_owner', pk=pk)
    if app.status == AdApplication.STATUS_APPROVED_FOR_PAYMENT:
        return redirect('advertisers:campaign_checkout', pk=pk)
    if app.status != AdApplication.STATUS_DRAFT:
        messages.info(request, 'Эта заявка уже не в черновике.')
        return redirect('advertisers:campaign_list')
    if not app.ord_wizard_saved_at:
        return redirect('advertisers:campaign_ord', pk=pk)

    if not app.contract_body_html:
        app.contract_body_html = build_contract_html(app)
        app.save(update_fields=['contract_body_html', 'updated_at'])

    if request.method == 'POST' and request.POST.get('action') == 'sign':
        app.contract_signed_at = timezone.now()
        app.contract_sign_ip = (request.META.get('REMOTE_ADDR') or '')[:45]
        app.save(update_fields=['contract_signed_at', 'contract_sign_ip', 'updated_at'])
        messages.success(request, 'Договор подписан электронно. Отправьте заявку владельцу канала на согласование.')
        return redirect('advertisers:campaign_contract', pk=app.pk)

    return render(request, 'advertisers/campaign_contract.html', {'app': app})


@login_required
def campaign_submit_to_owner(request, pk: int):
    """После подписи договора — отправка владельцу (до выставления счёта)."""
    app = _app_adv(request, pk)
    if request.method != 'POST':
        return redirect('advertisers:campaign_contract', pk=pk)
    if app.status != AdApplication.STATUS_DRAFT:
        messages.info(request, 'Заявка уже отправлена.')
        return redirect('advertisers:campaign_list')
    if not app.contract_signed_at:
        messages.error(request, 'Сначала подпишите договор.')
        return redirect('advertisers:campaign_contract', pk=pk)
    app.status = AdApplication.STATUS_PENDING_OWNER
    app.submitted_to_owner_at = timezone.now()
    app.owner_last_rejection_reason = ''
    app.save(
        update_fields=[
            'status',
            'submitted_to_owner_at',
            'owner_last_rejection_reason',
            'updated_at',
        ]
    )
    messages.success(
        request,
        'Заявка отправлена владельцу канала. После одобрения вы сможете перейти к оплате.',
    )
    return redirect('advertisers:campaign_pending_owner', pk=pk)


@login_required
def campaign_pending_owner(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_PENDING_OWNER:
        if app.status == AdApplication.STATUS_APPROVED_FOR_PAYMENT:
            return redirect('advertisers:campaign_checkout', pk=pk)
        return redirect('advertisers:campaign_list')
    return render(request, 'advertisers/campaign_pending_owner.html', {'app': app})


@login_required
def campaign_contacts(request, pk: int):
    """Контакты владельца канала и рекламодателя по заявке."""
    app = _app_adv(request, pk)
    owner = app.channel.owner
    return render(
        request,
        'advertisers/campaign_contacts.html',
        {'app': app, 'owner_user': owner, 'adv': app.advertiser},
    )


@login_required
def campaign_checkout(request, pk: int):
    app = _app_adv(request, pk)
    if app.status == AdApplication.STATUS_PENDING_OWNER:
        messages.info(request, 'Дождитесь одобрения владельца канала.')
        return redirect('advertisers:campaign_pending_owner', pk=pk)
    if app.status != AdApplication.STATUS_APPROVED_FOR_PAYMENT:
        if app.status == AdApplication.STATUS_DRAFT:
            messages.info(
                request,
                'После подписания договора отправьте заявку владельцу на согласование — затем откроется оплата.',
            )
            return redirect('advertisers:campaign_contract', pk=pk)
        messages.info(request, 'Оплата для этой заявки недоступна.')
        return redirect('advertisers:campaign_list')
    if not app.ord_wizard_saved_at:
        return redirect('advertisers:campaign_ord', pk=pk)
    if not app.contract_signed_at:
        messages.error(request, 'Сначала подпишите договор.')
        return redirect('advertisers:campaign_contract', pk=app.pk)

    owner = app.channel.owner
    pay_phone = (getattr(owner, 'ad_payment_phone', None) or '').strip()
    pay_instr = (getattr(owner, 'ad_payment_instructions', None) or '').strip()
    offered = (app.owner_offered_payment_method or '').strip()

    if request.method == 'POST':
        if offered in (AdApplication.PAY_TRANSFER, AdApplication.PAY_TBANK):
            method = offered
        else:
            method = (request.POST.get('payment_method') or '').strip()
        if method not in (AdApplication.PAY_TRANSFER, AdApplication.PAY_TBANK):
            messages.error(request, 'Выберите способ оплаты.')
            return render(
                request,
                'advertisers/campaign_checkout.html',
                {
                    'app': app,
                    'pay_phone': pay_phone,
                    'pay_instr': pay_instr,
                    'offered_payment_method': offered,
                },
            )

        if (
            method == AdApplication.PAY_TRANSFER
            and offered == AdApplication.PAY_TRANSFER
            and not (
                (app.transfer_dest_card_number or '').strip()
                and (app.transfer_dest_bank_name or '').strip()
                and (app.transfer_dest_recipient_hint or '').strip()
            )
        ):
            messages.error(
                request,
                'Реквизиты перевода не заполнены владельцем канала. Напишите ему — после обновления заявки оплата откроется.',
            )
            return render(
                request,
                'advertisers/campaign_checkout.html',
                {
                    'app': app,
                    'pay_phone': pay_phone,
                    'pay_instr': pay_instr,
                    'offered_payment_method': offered,
                },
            )

        if not app.invoice_id:
            inv = Invoice.objects.create(
                user=app.advertiser.user,
                channel=None,
                amount=app.total_amount,
                description=f'Реклама, заявка #{app.pk} — {app.channel.name}',
                status=Invoice.STATUS_DRAFT,
            )
            app.invoice = inv
            app.save(update_fields=['invoice', 'updated_at'])

        app.payment_method = method
        app.status = AdApplication.STATUS_AWAITING_PAYMENT
        app.save(update_fields=['payment_method', 'status', 'updated_at'])

        if method == AdApplication.PAY_TRANSFER:
            app.invoice.status = Invoice.STATUS_SENT
            app.invoice.save(update_fields=['status'])
            return redirect('advertisers:campaign_transfer_wait', pk=app.pk)

        # TBank
        try:
            from billing.tbank import TBankClient

            client = TBankClient()
            result = client.init_payment(
                order_id=str(app.invoice.pk),
                amount=int(app.invoice.amount * 100),
                description=app.invoice.description,
                customer_email=app.advertiser.user.email or '',
            )
            if result.get('Success'):
                app.invoice.tbank_payment_id = result.get('PaymentId', '')
                app.invoice.status = Invoice.STATUS_SENT
                app.invoice.save(update_fields=['tbank_payment_id', 'status'])
                return redirect(result.get('PaymentURL', '/billing/invoices/'))
            messages.error(request, result.get('Message', 'Ошибка инициализации оплаты'))
        except Exception as e:
            logger.exception('TBank init for campaign')
            messages.error(request, str(e)[:500])

    return render(
        request,
        'advertisers/campaign_checkout.html',
        {
            'app': app,
            'pay_phone': pay_phone,
            'pay_instr': pay_instr,
            'offered_payment_method': offered,
        },
    )


@login_required
def campaign_transfer_wait(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_AWAITING_PAYMENT or app.payment_method != AdApplication.PAY_TRANSFER:
        return redirect('advertisers:campaign_list')

    if request.method == 'POST' and request.FILES.get('transfer_screenshot'):
        f = request.FILES['transfer_screenshot']
        if f.size > 6 * 1024 * 1024:
            messages.error(request, 'Файл слишком большой (максимум 6 МБ).')
        else:
            ct = (getattr(f, 'content_type', '') or '').lower()
            if ct not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
                messages.error(request, 'Допустимы изображения: JPEG, PNG, WebP или GIF.')
            else:
                if app.transfer_screenshot:
                    app.transfer_screenshot.delete(save=False)
                app.transfer_screenshot = f
                app.save(update_fields=['transfer_screenshot', 'updated_at'])
                messages.success(request, 'Скриншот сохранён. Владелец канала увидит его при подтверждении оплаты.')
        return redirect('advertisers:campaign_transfer_wait', pk=pk)

    owner = app.channel.owner
    return render(
        request,
        'advertisers/campaign_transfer_wait.html',
        {
            'app': app,
            'pay_phone': (getattr(owner, 'ad_payment_phone', None) or '').strip(),
            'pay_instr': (getattr(owner, 'ad_payment_instructions', None) or '').strip(),
        },
    )


@login_required
def owner_campaign_detail(request, pk: int):
    if not (request.user.is_staff or request.user.role == request.user.ROLE_OWNER):
        messages.error(request, 'Нет доступа.')
        return redirect('dashboard')
    app = get_object_or_404(
        AdApplication.objects.select_related('advertiser', 'channel', 'channel__owner', 'post')
        .prefetch_related('post__media_files', 'campaign_posts'),
        pk=pk,
        channel__owner=request.user,
    )

    from ord_marking.models import ORDRegistration

    app_channel_id = app.channel_id
    ord_rows = []
    for p in app.campaign_posts.all()[:50]:
        for r in (
            ORDRegistration.objects.filter(post=p, channel_id=app_channel_id)
            .select_related('channel')
        ):
            ord_rows.append(r)

    return render(
        request,
        'advertisers/owner_campaign_detail.html',
        {'app': app, 'ord_rows': ord_rows},
    )


@login_required
def owner_campaign_decision(request, pk: int):
    if not (request.user.is_staff or request.user.role == request.user.ROLE_OWNER):
        messages.error(request, 'Нет доступа.')
        return redirect('dashboard')
    app = get_object_or_404(AdApplication, pk=pk, channel__owner=request.user)
    if request.method != 'POST':
        return redirect('advertisers:owner_campaign_detail', pk=pk)
    if app.status != AdApplication.STATUS_PENDING_OWNER:
        messages.error(request, 'Заявка не на согласовании или решение уже принято.')
        return redirect('advertisers:owner_campaign_detail', pk=pk)

    action = (request.POST.get('action') or '').strip()
    if action == 'approve':
        offer = (request.POST.get('owner_payment_method') or '').strip()
        if offer not in (AdApplication.PAY_TRANSFER, AdApplication.PAY_TBANK):
            messages.error(request, 'Выберите способ оплаты: перевод на карту или оплата картой через T-Bank.')
            return redirect('advertisers:owner_campaign_detail', pk=pk)
        if offer == AdApplication.PAY_TBANK:
            from core.models import get_global_api_keys

            keys = get_global_api_keys()
            if not (keys.get_tbank_terminal_key() or '').strip() or not (keys.get_tbank_secret_key() or '').strip():
                messages.error(
                    request,
                    'Для приёма оплаты картой задайте ключи T-Bank в «Ключи API» или выберите перевод на карту.',
                )
                return redirect('advertisers:owner_campaign_detail', pk=pk)
            card = ''
            bank = ''
            hint = ''
        else:
            card = (request.POST.get('transfer_card_number') or '').strip()
            bank = (request.POST.get('transfer_bank_name') or '').strip()
            hint = (request.POST.get('transfer_recipient_hint') or '').strip()
            if not card or not bank or not hint:
                messages.error(
                    request,
                    'Для перевода укажите номер карты или телефона, банк и получателя (имя и первая буква фамилии).',
                )
                return redirect('advertisers:owner_campaign_detail', pk=pk)
        app.status = AdApplication.STATUS_APPROVED_FOR_PAYMENT
        app.owner_approved_at = timezone.now()
        app.owner_offered_payment_method = offer
        app.transfer_dest_card_number = card[:64] if offer == AdApplication.PAY_TRANSFER else ''
        app.transfer_dest_bank_name = bank[:255] if offer == AdApplication.PAY_TRANSFER else ''
        app.transfer_dest_recipient_hint = hint[:120] if offer == AdApplication.PAY_TRANSFER else ''
        app.save(
            update_fields=[
                'status',
                'owner_approved_at',
                'owner_offered_payment_method',
                'transfer_dest_card_number',
                'transfer_dest_bank_name',
                'transfer_dest_recipient_hint',
                'updated_at',
            ]
        )
        messages.success(
            request,
            'Заявка одобрена. Рекламодатель получит выбранный способ оплаты в кабинете.',
        )
    elif action == 'reject':
        reason = (request.POST.get('reason') or '').strip()
        if len(reason) < 3:
            messages.error(request, 'Укажите причину отказа (не меньше 3 символов).')
            return redirect('advertisers:owner_campaign_detail', pk=pk)
        if app.transfer_screenshot:
            app.transfer_screenshot.delete(save=False)
        app.transfer_screenshot = None
        app.status = AdApplication.STATUS_DRAFT
        app.owner_last_rejection_reason = reason[:4000]
        app.submitted_to_owner_at = None
        app.owner_approved_at = None
        app.owner_offered_payment_method = ''
        app.transfer_dest_card_number = ''
        app.transfer_dest_bank_name = ''
        app.transfer_dest_recipient_hint = ''
        app.save(
            update_fields=[
                'transfer_screenshot',
                'status',
                'owner_last_rejection_reason',
                'submitted_to_owner_at',
                'owner_approved_at',
                'owner_offered_payment_method',
                'transfer_dest_card_number',
                'transfer_dest_bank_name',
                'transfer_dest_recipient_hint',
                'updated_at',
            ]
        )
        messages.success(request, 'Отказ отправлен рекламодателю. Он сможет внести правки и снова отправить заявку.')
    else:
        messages.error(request, 'Неизвестное действие.')
    return redirect('advertisers:owner_campaign_detail', pk=pk)


@login_required
def owner_campaign_confirm_payment(request, pk: int):
    if not (request.user.is_staff or request.user.role == request.user.ROLE_OWNER):
        messages.error(request, 'Нет доступа.')
        return redirect('dashboard')
    app = get_object_or_404(AdApplication, pk=pk, channel__owner=request.user)

    if request.method != 'POST':
        return redirect('advertisers:owner_campaign_detail', pk=pk)

    if app.payment_method != AdApplication.PAY_TRANSFER or not app.invoice_id:
        messages.error(request, 'Подтверждение доступно только для заявок с переводом и счётом.')
        return redirect('advertisers:owner_campaign_detail', pk=pk)

    app.transfer_marked_received = True
    app.invoice.status = Invoice.STATUS_PAID
    app.invoice.paid_at = timezone.now()
    app.invoice.save(update_fields=['status', 'paid_at'])
    app.status = AdApplication.STATUS_PAID
    app.save(update_fields=['transfer_marked_received', 'status', 'updated_at'])
    try:
        fulfill_paid_ad_application(app)
        messages.success(request, 'Оплата подтверждена, посты поставлены в расписание.')
    except Exception as e:
        logger.exception('fulfill after transfer')
        messages.warning(request, f'Оплата отмечена, но публикации нужно проверить: {e}')
    return redirect('advertisers:owner_campaign_detail', pk=pk)
