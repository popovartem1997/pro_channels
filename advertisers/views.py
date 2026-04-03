"""
Кабинет рекламодателя + панель владельца для модерации заявок.
"""
import re

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from .models import Advertiser, AdvertisingOrder, Act
from .placement_channels import channels_for_placement_display
from billing.models import Invoice
from .services import ensure_ad_post_for_order


def catalog(request):
    """Публичный каталог каналов для рекламы."""
    from urllib.parse import urlencode

    from django.db.models import Prefetch
    from django.urls import reverse

    from channels.models import Channel, ChannelAdAddon
    from django.contrib.auth import get_user_model

    User = get_user_model()
    owner_ids = list(User.objects.filter(role=User.ROLE_OWNER).values_list('id', flat=True))
    addon_qs = ChannelAdAddon.objects.filter(is_active=True).order_by('title', 'pk')
    channels = list(
        Channel.objects.filter(is_active=True, ad_enabled=True, owner_id__in=owner_ids)
        .prefetch_related(Prefetch('ad_addons', queryset=addon_qs))
        .order_by('-subscribers_count')
    )
    new_path = reverse('advertisers:campaign_new')
    login_base = reverse('login')
    for ch in channels:
        next_path = f'{new_path}?channel={ch.pk}'
        ch.advertiser_login_url = f'{login_base}?{urlencode({"next": next_path})}'
    return render(request, 'advertisers/catalog.html', {'channels': channels})


def _advertiser_register_next(request):
    if request.method == 'POST':
        return (request.POST.get('next') or '').strip()
    return (request.GET.get('next') or '').strip()


_REG_PREFILL_KEYS = (
    'email',
    'password1',
    'password2',
    'first_name',
    'phone',
    'company_name',
    'inn',
    'legal_address',
    'actual_address',
    'contact_person',
    'kpp',
    'ogrn',
    'bank_name',
    'bank_account',
    'bank_bik',
    'bank_corr_account',
    'ord_model_scheme',
)


def _advertiser_register_context(request, next_redirect=None):
    ctx = {'next_redirect': next_redirect if next_redirect is not None else _advertiser_register_next(request)}
    prefill = {k: '' for k in _REG_PREFILL_KEYS}
    if request.method == 'POST':
        for k in _REG_PREFILL_KEYS:
            prefill[k] = (request.POST.get(k) or '').strip()
        prefill['password1'] = ''
        prefill['password2'] = ''
    elif request.user.is_authenticated:
        prefill['first_name'] = (request.user.first_name or '').strip()
        prefill['phone'] = (getattr(request.user, 'phone', None) or '').strip()
    ctx['prefill'] = prefill
    ctx['ord_scheme_choices'] = Advertiser.ORD_MODEL_SCHEME_CHOICES
    return ctx


def advertiser_register(request):
    """Регистрация профиля рекламодателя."""
    if request.user.is_authenticated and hasattr(request.user, 'advertiser_profile'):
        return redirect('advertisers:campaign_list')

    if request.method == 'POST':
        next_redirect = (request.POST.get('next') or '').strip()
        next_url = next_redirect
        # Если пользователь не залогинен — создаём аккаунт рекламодателя сразу здесь,
        # чтобы после регистрации next вёл в мастер заявки, а не в 404.
        if not request.user.is_authenticated:
            from django.contrib.auth import get_user_model, login
            User = get_user_model()
            email = request.POST.get('email', '').strip().lower()
            password1 = request.POST.get('password1', '')
            password2 = request.POST.get('password2', '')
            first_name = request.POST.get('first_name', '').strip()
            phone = request.POST.get('phone', '').strip()

            if not email or '@' not in email:
                messages.error(request, 'Укажите корректный email.')
                return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))
            if not password1 or password1 != password2 or len(password1) < 8:
                messages.error(request, 'Проверьте пароль (минимум 8 символов) и совпадение паролей.')
                return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))
            if len(first_name) < 2:
                messages.error(request, 'Укажите имя (не менее 2 символов).')
                return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))
            digits_phone = re.sub(r'\D', '', phone)
            if len(digits_phone) < 10:
                messages.error(request, 'Укажите телефон (не менее 10 цифр).')
                return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))
            if User.objects.filter(email=email).exists():
                messages.error(request, 'Пользователь с таким email уже существует. Войдите в аккаунт.')
                return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))

            user = User(
                email=email,
                username=email,
                first_name=first_name[:150],
                phone=phone[:20],
                company=request.POST.get('company_name', '').strip(),
                role=User.ROLE_ADVERTISER,
                is_email_verified=False,
            )
            user.set_password(password1)
            user.save()
            try:
                from accounts.models import EmailVerification
                from accounts.views import _send_verification_email
                verification = EmailVerification.objects.create(user=user)
                _send_verification_email(user, verification, request)
            except Exception:
                pass
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')

        first_name = request.POST.get('first_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        if len(first_name) < 2:
            messages.error(request, 'Укажите имя (не менее 2 символов).')
            return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))
        digits_phone = re.sub(r'\D', '', phone)
        if len(digits_phone) < 10:
            messages.error(request, 'Укажите телефон (не менее 10 цифр).')
            return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))

        if request.user.is_authenticated:
            u = request.user
            u.first_name = first_name[:150]
            u.phone = phone[:20]
            if not (u.company or '').strip():
                u.company = (request.POST.get('company_name', '') or '').strip()[:255]
            u.save(update_fields=['first_name', 'phone', 'company'])

        company_name = request.POST.get('company_name', '').strip()
        inn = request.POST.get('inn', '').strip()
        legal_address = request.POST.get('legal_address', '').strip()
        actual_address = request.POST.get('actual_address', '').strip()
        contact_person = request.POST.get('contact_person', '').strip()
        bank_name = request.POST.get('bank_name', '').strip()
        bank_account = request.POST.get('bank_account', '').strip()
        bank_bik = request.POST.get('bank_bik', '').strip()
        bank_corr = request.POST.get('bank_corr_account', '').strip()
        ord_scheme = (request.POST.get('ord_model_scheme') or '').strip()
        valid_schemes = {x[0] for x in Advertiser.ORD_MODEL_SCHEME_CHOICES}
        if ord_scheme not in valid_schemes:
            ord_scheme = ''

        if not all([company_name, inn, legal_address, contact_person]):
            messages.error(request, 'Заполните все обязательные поля организации и контакта.')
            return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))
        if not inn.isdigit() or len(inn) not in (10, 12):
            messages.error(request, 'ИНН должен состоять из 10 или 12 цифр.')
            return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))
        adv = Advertiser.objects.create(
            user=request.user,
            company_name=company_name,
            inn=inn,
            kpp=request.POST.get('kpp', '').strip(),
            ogrn=request.POST.get('ogrn', '').strip(),
            legal_address=legal_address,
            actual_address=actual_address,
            contact_person=contact_person,
            contact_phone=phone[:20],
            bank_name=bank_name,
            bank_account=bank_account,
            bank_bik=bank_bik,
            bank_corr_account=bank_corr,
            ord_model_scheme=ord_scheme,
        )
        if getattr(request.user, 'role', '') != request.user.ROLE_ADVERTISER:
            request.user.role = request.user.ROLE_ADVERTISER
            request.user.save(update_fields=['role'])
        msg_ok = 'Профиль рекламодателя создан.'
        try:
            from core.models import get_global_api_keys
            from advertisers.ord_provision import ensure_advertiser_ord_profile

            keys = get_global_api_keys()
            ord_res = ensure_advertiser_ord_profile(
                adv, use_sandbox=bool(getattr(keys, 'vk_ord_use_sandbox', False))
            )
            if ord_res.get('ok'):
                msg_ok += ' Данные отправлены в ВК ОРД (контрагент).'
                if ord_res.get('contract_id'):
                    msg_ok += ' Договор в ОРД создан или обновлён.'
            elif ord_res.get('error'):
                msg_ok += f' Автоотправка в ОРД: {ord_res["error"][:280]}'
            if ord_res.get('contract_error'):
                msg_ok += f' Договор в ОРД: {ord_res["contract_error"][:220]}'
        except Exception:
            pass
        messages.success(request, msg_ok)
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
        return redirect('advertisers:campaign_list')

    return render(request, 'advertisers/register.html', _advertiser_register_context(request))


@login_required
def advertiser_dashboard(request):
    """Старый URL /advertisers/ — ведёт на список заявок (новый поток)."""
    return redirect('advertisers:campaign_list')


@login_required
def order_detail(request, pk):
    try:
        adv = Advertiser.objects.get(user=request.user)
    except Advertiser.DoesNotExist:
        messages.info(request, 'Сначала заполните профиль рекламодателя.')
        return redirect('advertisers:register')
    order = get_object_or_404(AdvertisingOrder, pk=pk, advertiser=adv)
    acts = Act.objects.filter(order=order)
    return render(request, 'advertisers/order_detail.html', {
        'order': order,
        'acts': acts,
        'invoice': order.invoice,
        'placement_channels': channels_for_placement_display(order.channels),
    })


# ──────────────────────────────────────────────────────────────────────────────
# Панель владельца: старые заказы AdvertisingOrder — только карточка / moderate по прямой ссылке;
# список /manage/ редиректит на заявки кампаний (см. urls).
# ──────────────────────────────────────────────────────────────────────────────


def _owner_order_moderate_redirect(request, order):
    if request.method == 'POST' and request.POST.get('return_to') == 'order_detail':
        return redirect('advertisers:owner_order_detail', pk=order.pk)
    return redirect('advertisers:owner_campaigns')


@login_required
def owner_order_detail(request, pk):
    """Полная информация по рекламной заявке (заказу) для владельца / staff."""
    if not (request.user.is_staff or request.user.role == request.user.ROLE_OWNER):
        messages.error(request, 'Нет доступа.')
        return redirect('dashboard')
    order = get_object_or_404(
        AdvertisingOrder.objects.select_related(
            'advertiser', 'advertiser__user', 'invoice', 'moderator', 'post',
        ).prefetch_related('channels'),
        pk=pk,
    )
    acts = Act.objects.filter(order=order).order_by('-issued_at')
    return render(request, 'advertisers/owner_order_detail.html', {
        'order': order,
        'acts': acts,
        'placement_channels': channels_for_placement_display(order.channels),
    })


@login_required
def owner_order_moderate(request, pk):
    """Одобрить/отклонить/запустить/завершить заявку.

    При одобрении создаём счёт Invoice, который рекламодатель сможет оплатить через TBank.
    """
    if not (request.user.is_staff or request.user.role == request.user.ROLE_OWNER):
        messages.error(request, 'Нет доступа.')
        return redirect('dashboard')
    order = get_object_or_404(AdvertisingOrder, pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            # Создаём счёт, если его ещё нет
            if not order.invoice:
                invoice = Invoice.objects.create(
                    user=order.advertiser.user,
                    amount=order.budget,
                    description=f'Рекламная кампания: {order.title}',
                )
                order.invoice = invoice
            order.status = AdvertisingOrder.STATUS_APPROVED
            order.moderator = request.user
            order.save(update_fields=['status', 'moderator', 'invoice'])
            messages.success(request, f'Заявка #{order.pk} одобрена. Счёт выставлен.')
        elif action == 'reject':
            reason = request.POST.get('rejection_reason', '').strip()
            order.status = AdvertisingOrder.STATUS_REJECTED
            order.rejection_reason = reason
            order.moderator = request.user
            order.save(update_fields=['status', 'rejection_reason'])
            messages.warning(request, f'Заявка #{order.pk} отклонена.')
        elif action == 'activate':
            # Старт кампании только после оплаты счёта рекламодателем
            if not order.invoice or order.invoice.status != Invoice.STATUS_PAID:
                messages.error(request, 'Нельзя запустить кампанию: счёт не оплачен.')
                return _owner_order_moderate_redirect(request, order)
            if order.status != AdvertisingOrder.STATUS_APPROVED:
                messages.error(request, 'Нельзя запустить кампанию из текущего статуса.')
                return _owner_order_moderate_redirect(request, order)
            order.status = AdvertisingOrder.STATUS_ACTIVE
            order.save(update_fields=['status'])
            try:
                ensure_ad_post_for_order(order)
            except Exception as e:
                messages.warning(request, f'Кампания активирована, но автопост не создан: {e}')
            messages.success(request, f'Заявка #{order.pk} переведена в "Выполняется".')
        elif action == 'complete':
            if order.status != AdvertisingOrder.STATUS_ACTIVE:
                messages.error(request, 'Нельзя завершить кампанию из текущего статуса.')
                return _owner_order_moderate_redirect(request, order)
            order.status = AdvertisingOrder.STATUS_COMPLETED
            order.save(update_fields=['status'])
            # Автогенерация акта (1 акт на заказ)
            if not Act.objects.filter(order=order).exists():
                from django.utils import timezone as tz
                act = Act.objects.create(
                    order=order,
                    amount=order.budget,
                    service_description=f'Размещение рекламы «{order.title}»',
                    issued_at=tz.now().date(),
                )
                from billing.pdf import generate_act_pdf
                generate_act_pdf(act)
            messages.success(request, f'Заявка #{order.pk} завершена.')
    return _owner_order_moderate_redirect(request, order)


@login_required
def download_act_pdf(request, pk):
    """Скачать PDF акта выполненных работ."""
    act = get_object_or_404(Act, pk=pk)
    # Доступ: рекламодатель или владелец/staff
    if act.order_id:
        adv = act.order.advertiser
    elif act.ad_application_id:
        adv = act.ad_application.advertiser
    else:
        messages.error(request, 'Некорректная запись акта.')
        return redirect('dashboard')
    if adv.user != request.user and not (request.user.is_staff or request.user.role == request.user.ROLE_OWNER):
        messages.error(request, 'Нет доступа.')
        return redirect('dashboard')
    if not act.pdf_file:
        from billing.pdf import generate_act_pdf
        generate_act_pdf(act)
    from django.http import FileResponse
    return FileResponse(act.pdf_file.open('rb'), as_attachment=True,
                        filename=f'act_{act.number}.pdf')


@login_required
def create_act(request, order_pk):
    """Создание акта для завершённого заказа (только owner/staff)."""
    if not (request.user.is_staff or request.user.role == request.user.ROLE_OWNER):
        messages.error(request, 'Нет доступа.')
        return redirect('dashboard')

    order = get_object_or_404(AdvertisingOrder, pk=order_pk)
    if request.method == 'POST':
        service_description = request.POST.get('service_description', '').strip()
        amount = request.POST.get('amount', str(order.budget))
        if not service_description:
            service_description = f'Размещение рекламы «{order.title}»'

        from django.utils import timezone as tz
        act = Act.objects.create(
            order=order,
            amount=amount,
            service_description=service_description,
            issued_at=tz.now().date(),
        )
        # Сразу генерируем PDF
        from billing.pdf import generate_act_pdf
        generate_act_pdf(act)
        messages.success(request, f'Акт {act.number} создан.')
        return redirect('advertisers:owner_campaigns')

    return render(request, 'advertisers/create_act.html', {'order': order})


# ──────────────────────────────────────────────────────────────────────────────
# Новый поток: заявка рекламодателя (мастер канал → слоты → контент → ОРД → оплата)
# ──────────────────────────────────────────────────────────────────────────────


@login_required
def ad_application_list(request):
    from .models import AdApplication

    try:
        adv = Advertiser.objects.get(user=request.user)
    except Advertiser.DoesNotExist:
        messages.info(request, 'Сначала заполните профиль рекламодателя.')
        return redirect('advertisers:register')
    from django.db.models import Count

    applications = (
        AdApplication.objects.filter(advertiser=adv)
        .select_related('channel', 'channel__owner', 'post', 'invoice')
        .prefetch_related('campaign_posts')
        .annotate(_n_campaign_posts=Count('campaign_posts', distinct=True))
        .order_by('-created_at')
    )
    return render(
        request,
        'advertisers/ad_application_list.html',
        {'advertiser': adv, 'applications': applications},
    )


def _owner_campaign_issue_labels(app):
    """Короткие метки незавершённости для доски владельца."""
    from content.models import Post
    from .models import AdApplication

    labels = []
    if app.status == AdApplication.STATUS_DRAFT:
        if app.owner_last_rejection_reason:
            labels.append('возврат на правки')
        if not app.selected_slot_ids:
            labels.append('слоты')
        post = app.post
        if not post or not (post.text or '').strip():
            labels.append('контент')
        if not app.ord_wizard_saved_at:
            labels.append('ОРД')
        if not app.contract_signed_at:
            labels.append('договор')
    elif app.status == AdApplication.STATUS_PENDING_OWNER:
        labels.append('ждёт ваше решение')
    elif app.status == AdApplication.STATUS_APPROVED_FOR_PAYMENT:
        labels.append('ждёт оплаты')
    elif app.status == AdApplication.STATUS_AWAITING_PAYMENT:
        if not app.invoice_id:
            labels.append('нет счёта')
    elif app.status in (
        AdApplication.STATUS_PAID,
        AdApplication.STATUS_SCHEDULED,
        AdApplication.STATUS_PUBLISHED,
    ):
        posts = list(app.campaign_posts.all())
        if not posts:
            labels.append('нет постов')
        else:
            pub = sum(1 for p in posts if p.status == Post.STATUS_PUBLISHED)
            if pub < len(posts):
                labels.append('публикация')
            if app.status == AdApplication.STATUS_PUBLISHED:
                for p in posts:
                    if p.status == Post.STATUS_PUBLISHED and not list(p.ord_registrations.all()):
                        labels.append('ОРД не заведён')
                        break
    return labels


@login_required
def owner_ad_applications(request):
    """Все заявки нового потока по каналам владельца."""
    from django.db.models import Count, Sum

    from .models import AdApplication
    if not (request.user.is_staff or request.user.role == request.user.ROLE_OWNER):
        messages.error(request, 'Нет доступа.')
        return redirect('dashboard')
    from channels.models import Channel

    ch_ids = Channel.objects.filter(owner=request.user).values_list('pk', flat=True)
    applications = list(
        AdApplication.objects.filter(channel_id__in=ch_ids)
        .select_related('advertiser', 'channel', 'post', 'invoice')
        .prefetch_related('campaign_posts__ord_registrations')
        .order_by('-created_at')
    )
    paid_like = [
        AdApplication.STATUS_PAID,
        AdApplication.STATUS_SCHEDULED,
        AdApplication.STATUS_PUBLISHED,
        AdApplication.STATUS_COMPLETED,
    ]
    rev = AdApplication.objects.filter(channel_id__in=ch_ids, status__in=paid_like).aggregate(
        s=Sum('total_amount'), c=Count('id')
    )
    pending = AdApplication.objects.filter(
        channel_id__in=ch_ids, status=AdApplication.STATUS_AWAITING_PAYMENT
    ).aggregate(s=Sum('total_amount'), c=Count('id'))
    pending_owner_count = AdApplication.objects.filter(
        channel_id__in=ch_ids, status=AdApplication.STATUS_PENDING_OWNER
    ).count()
    by_channel = (
        AdApplication.objects.filter(channel_id__in=ch_ids, status__in=paid_like)
        .values('channel__name')
        .annotate(revenue=Sum('total_amount'), cnt=Count('id'))
        .order_by('-revenue')
    )
    app_issues = {a.pk: _owner_campaign_issue_labels(a) for a in applications}
    return render(
        request,
        'advertisers/owner_ad_applications.html',
        {
            'applications': applications,
            'stats_paid_revenue': rev['s'] or 0,
            'stats_paid_count': rev['c'] or 0,
            'stats_pending_sum': pending['s'] or 0,
            'stats_pending_count': pending['c'] or 0,
            'stats_pending_owner_count': pending_owner_count,
            'stats_by_channel': list(by_channel),
            'app_issues': app_issues,
        },
    )
