"""
Мастер заявки на рекламу: канал → слоты → контент → ОРД → проверка → договор → оплата.
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from billing.models import Invoice
from channels.models import Channel, ChannelAdAddon

from .ad_campaign_services import (
    book_slots_for_application,
    build_contract_html,
    ensure_ad_slots_for_channel,
    ensure_draft_post_for_application,
    fulfill_paid_ad_application,
    save_pricing_to_application,
)
from .models import AdApplication, Advertiser

logger = logging.getLogger(__name__)


def _advertiser(request):
    return get_object_or_404(Advertiser, user=request.user)


def _app_adv(request, pk: int) -> AdApplication:
    adv = _advertiser(request)
    return get_object_or_404(AdApplication, pk=pk, advertiser=adv)


def _owner_allowed_channels_queryset(request):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    owner_ids = list(User.objects.filter(role=User.ROLE_OWNER).values_list('id', flat=True))
    return Channel.objects.filter(is_active=True, owner_id__in=owner_ids, ad_enabled=True)


@login_required
def campaign_resume(request, pk: int):
    """Продолжить черновик с первого незаполненного шага."""
    app = _app_adv(request, pk)
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
    try:
        adv = Advertiser.objects.get(user=request.user)
    except Advertiser.DoesNotExist:
        messages.info(request, 'Сначала заполните профиль рекламодателя.')
        return redirect('advertisers:register')

    channels = _owner_allowed_channels_queryset(request).order_by('name')
    if request.method == 'POST':
        raw = (request.POST.get('channel_id') or '').strip()
        if not raw.isdigit():
            messages.error(request, 'Выберите канал.')
            return render(request, 'advertisers/campaign_channel.html', {'channels': channels})
        ch = get_object_or_404(channels, pk=int(raw))
        app = AdApplication.objects.create(
            advertiser=adv,
            channel=ch,
            status=AdApplication.STATUS_DRAFT,
        )
        return redirect('advertisers:campaign_slots', pk=app.pk)

    return render(request, 'advertisers/campaign_channel.html', {'channels': channels})


@login_required
def campaign_slots(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        messages.info(request, 'Эта заявка уже не в черновике.')
        return redirect('advertisers:campaign_list')

    ensure_ad_slots_for_channel(app.channel)
    from advertisers.models import AdvertisingSlot

    if request.method == 'POST':
        sel = request.POST.getlist('slot_ids')
        try:
            book_slots_for_application(app, [int(x) for x in sel if str(x).isdigit()])
            app.refresh_from_db()
            n = len(app.selected_slot_ids or [])
            if n < 1:
                messages.error(request, 'Выберите хотя бы один слот.')
            else:
                save_pricing_to_application(app, slot_count=n, addon_codes=app.addon_codes or [])
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
        {'app': app, 'slots': slots, 'selected': selected},
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


@login_required
def campaign_ord(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        return redirect('advertisers:campaign_list')

    if request.method == 'POST':
        app.ord_contract_external_id = (request.POST.get('ord_contract_external_id') or '').strip()
        app.ord_person_external_id = (request.POST.get('ord_person_external_id') or '').strip()
        app.ord_pad_external_id = (request.POST.get('ord_pad_external_id') or '').strip()
        app.ord_sync_error = ''
        if app.ord_contract_external_id or app.ord_person_external_id or app.ord_pad_external_id:
            app.ord_synced_at = timezone.now()
        app.ord_wizard_saved_at = timezone.now()
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
        messages.success(request, 'Данные ОРД сохранены.')
        return redirect('advertisers:campaign_review', pk=app.pk)

    return render(request, 'advertisers/campaign_ord.html', {'app': app})


@login_required
def campaign_review(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        return redirect('advertisers:campaign_list')
    if not app.ord_wizard_saved_at:
        messages.info(request, 'Сначала пройдите шаг с данными ОРД (можно оставить поля пустыми и нажать «Далее»).')
        return redirect('advertisers:campaign_ord', pk=pk)

    n = len(app.selected_slot_ids or [])
    if n:
        save_pricing_to_application(app, slot_count=n, addon_codes=app.addon_codes or [])
        app.refresh_from_db()

    addons = ChannelAdAddon.objects.filter(channel=app.channel, is_active=True).order_by('title')
    if request.method == 'POST':
        codes = request.POST.getlist('addon_codes')
        n = len(app.selected_slot_ids or [])
        save_pricing_to_application(app, slot_count=max(1, n), addon_codes=codes)
        app.refresh_from_db()
        messages.success(request, 'Параметры обновлены.')
        return redirect('advertisers:campaign_contract', pk=app.pk)

    return render(request, 'advertisers/campaign_review.html', {'app': app, 'addons': addons})


@login_required
def campaign_contract(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
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
        messages.success(request, 'Договор подписан электронно.')
        return redirect('advertisers:campaign_checkout', pk=app.pk)

    return render(request, 'advertisers/campaign_contract.html', {'app': app})


@login_required
def campaign_checkout(request, pk: int):
    app = _app_adv(request, pk)
    if app.status != AdApplication.STATUS_DRAFT:
        return redirect('advertisers:campaign_list')
    if not app.ord_wizard_saved_at:
        return redirect('advertisers:campaign_ord', pk=pk)
    if not app.contract_signed_at:
        messages.error(request, 'Сначала подпишите договор.')
        return redirect('advertisers:campaign_contract', pk=app.pk)

    owner = app.channel.owner
    pay_phone = (getattr(owner, 'ad_payment_phone', None) or '').strip()
    pay_instr = (getattr(owner, 'ad_payment_instructions', None) or '').strip()

    if request.method == 'POST':
        method = (request.POST.get('payment_method') or '').strip()
        if method not in (AdApplication.PAY_TRANSFER, AdApplication.PAY_TBANK):
            messages.error(request, 'Выберите способ оплаты.')
            return render(
                request,
                'advertisers/campaign_checkout.html',
                {'app': app, 'pay_phone': pay_phone, 'pay_instr': pay_instr},
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
        {'app': app, 'pay_phone': pay_phone, 'pay_instr': pay_instr},
    )


@login_required
def campaign_transfer_wait(request, pk: int):
    app = _app_adv(request, pk)
    if app.payment_method != AdApplication.PAY_TRANSFER:
        return redirect('advertisers:campaign_list')
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
    app = get_object_or_404(AdApplication, pk=pk, channel__owner=request.user)

    from ord_marking.models import ORDRegistration

    ord_rows = []
    for p in app.campaign_posts.all()[:50]:
        for r in ORDRegistration.objects.filter(post=p).select_related('channel'):
            ord_rows.append(r)

    return render(
        request,
        'advertisers/owner_campaign_detail.html',
        {'app': app, 'ord_rows': ord_rows},
    )


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
