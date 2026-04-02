"""
Биллинг: подписка, счета, оплата.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from .models import Plan, Invoice, Payment, SubscriptionPurchase
from channels.models import Channel
import json
import hashlib
import logging

logger = logging.getLogger(__name__)


@login_required
def subscribe(request):
    """Страница подписки на канал."""
    plans = Plan.objects.filter(is_active=True, code='basic')
    user_channels = list(Channel.objects.filter(owner=request.user))
    subscriptions = SubscriptionPurchase.objects.filter(
        user=request.user, is_active=True, ends_at__gt=timezone.now()
    ).select_related('channel', 'plan')

    active_sub_by_channel = {s.channel_id: s for s in subscriptions}
    for ch in user_channels:
        ch.active_subscription = active_sub_by_channel.get(ch.pk)

    return render(request, 'billing/subscribe.html', {
        'plans': plans,
        'user_channels': user_channels,
        'subscriptions': subscriptions,
    })


@login_required
def create_invoice(request):
    """Создать счёт и инициировать платёж через TBank."""
    if request.method != 'POST':
        return redirect('billing:subscribe')

    channel_id = request.POST.get('channel_id')
    channel = get_object_or_404(Channel, pk=channel_id, owner=request.user)

    plan = Plan.objects.filter(code='basic', is_active=True).first()
    if not plan:
        messages.error(request, 'Тариф не найден.')
        return redirect('billing:subscribe')

    # Создать счёт
    invoice = Invoice.objects.create(
        user=request.user,
        channel=channel,
        amount=plan.price,
        description=f'Подписка на канал "{channel.name}" — {plan.name}',
    )

    # Инициировать платёж в TBank
    from .tbank import TBankClient
    client = TBankClient()
    result = client.init_payment(
        order_id=str(invoice.pk),
        amount=int(plan.price * 100),  # в копейках
        description=invoice.description,
        customer_email=request.user.email,
    )

    if result.get('Success'):
        invoice.tbank_payment_id = result.get('PaymentId', '')
        invoice.tbank_order_id = result.get('OrderId', '')
        invoice.status = Invoice.STATUS_SENT
        invoice.save()
        # Редирект на страницу оплаты TBank
        return redirect(result.get('PaymentURL', '/billing/'))
    else:
        invoice.delete()
        messages.error(request, f'Ошибка платёжной системы: {result.get("Message", "Неизвестная ошибка")}')
        return redirect('billing:subscribe')


@csrf_exempt
def tbank_webhook(request):
    """Webhook от TBank — обработка результата платежа."""
    if request.method != 'POST':
        return HttpResponse('OK')

    try:
        data = json.loads(request.body)
    except Exception:
        return HttpResponse('OK')

    # Проверка подписи (Token) от TBank
    from .tbank import TBankClient
    client = TBankClient()
    received_token = data.get('Token', '')
    expected_token = client._get_token(data)
    if received_token != expected_token:
        logger.warning(f'TBank webhook: неверная подпись. Received={received_token}, Expected={expected_token}')
        return HttpResponse('OK', status=400)

    payment_id = data.get('PaymentId', '')
    status = data.get('Status', '')
    order_id = data.get('OrderId', '')

    logger.info(f'TBank webhook: PaymentId={payment_id}, Status={status}, OrderId={order_id}')

    if status == 'CONFIRMED' and order_id:
        try:
            invoice = Invoice.objects.get(pk=int(order_id))
            invoice.status = Invoice.STATUS_PAID
            invoice.paid_at = timezone.now()
            invoice.save()

            payment = Payment.objects.create(
                invoice=invoice,
                tbank_payment_id=payment_id,
                amount=invoice.amount,
                status=Payment.STATUS_CONFIRMED,
                confirmed_at=timezone.now(),
                raw_response=data,
            )

            from advertisers.ad_campaign_services import fulfill_paid_ad_application
            from advertisers.models import AdApplication

            # Счёт за рекламу (AdApplication) — не подписка и не старый AdvertisingOrder
            ad_app = AdApplication.objects.filter(invoice_id=invoice.pk).first()
            if ad_app and ad_app.status == AdApplication.STATUS_AWAITING_PAYMENT:
                ad_app.status = AdApplication.STATUS_PAID
                ad_app.save(update_fields=['status', 'updated_at'])
                try:
                    ok = fulfill_paid_ad_application(ad_app)
                    if not ok:
                        logger.error('Fulfill AdApplication #%s после TBank вернул False', ad_app.pk)
                except Exception as ex:
                    logger.error('Fulfill AdApplication после TBank: %s', ex)
            elif invoice.channel_id:
                plan = Plan.objects.filter(code='basic', is_active=True).first()
                if plan:
                    starts_at = timezone.now()
                    ends_at = starts_at + timezone.timedelta(days=plan.duration_days)
                    SubscriptionPurchase.objects.create(
                        user=invoice.user,
                        channel=invoice.channel,
                        payment=payment,
                        plan=plan,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        is_active=True,
                        auto_renew=True,
                    )
            else:
                try:
                    adv_order = invoice.advertising_order
                    if adv_order.status == adv_order.STATUS_APPROVED:
                        adv_order.status = adv_order.STATUS_ACTIVE
                        adv_order.save(update_fields=['status'])
                    try:
                        from advertisers.services import ensure_ad_post_for_order

                        ensure_ad_post_for_order(adv_order)
                    except Exception as e:
                        logger.error('Автопост рекламного заказа #%s: %s', adv_order.pk, e)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f'Ошибка обработки webhook TBank: {e}')

    return HttpResponse('OK')


@login_required
def invoices_list(request):
    """Список счетов пользователя."""
    invoices = Invoice.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'billing/invoices.html', {'invoices': invoices})


@login_required
def pay_invoice(request, pk):
    """Оплатить существующий счёт через TBank."""
    invoice = get_object_or_404(Invoice, pk=pk, user=request.user)
    if invoice.status == Invoice.STATUS_PAID:
        messages.info(request, 'Счёт уже оплачен.')
        return redirect('billing:invoices')

    from .tbank import TBankClient
    client = TBankClient()
    result = client.init_payment(
        order_id=str(invoice.pk),
        amount=int(invoice.amount * 100),
        description=invoice.description,
        customer_email=request.user.email,
    )
    if result.get('Success'):
        invoice.tbank_payment_id = result.get('PaymentId', '')
        invoice.status = Invoice.STATUS_SENT
        invoice.save(update_fields=['tbank_payment_id', 'status'])
        return redirect(result.get('PaymentURL', '/billing/invoices/'))
    else:
        messages.error(request, f'Ошибка оплаты: {result.get("Message", "Неизвестная ошибка")}')
        return redirect('billing:invoices')


@login_required
def payment_success(request):
    messages.success(request, 'Оплата прошла успешно. Мы подтвердим платёж и активируем услуги.')
    return redirect('dashboard')


@login_required
def payment_fail(request):
    messages.error(request, 'Оплата не завершена. Попробуйте ещё раз.')
    return redirect('billing:subscribe')


@login_required
def download_invoice_pdf(request, pk):
    """Скачать PDF счёта. Генерирует при первом обращении."""
    invoice = get_object_or_404(Invoice, pk=pk, user=request.user)
    if not invoice.pdf_file:
        from .pdf import generate_invoice_pdf
        generate_invoice_pdf(invoice)
    from django.http import FileResponse
    return FileResponse(invoice.pdf_file.open('rb'), as_attachment=True,
                        filename=f'invoice_{invoice.number}.pdf')
