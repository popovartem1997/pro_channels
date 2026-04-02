"""
Кабинет рекламодателя + панель владельца для модерации заявок.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from .models import Advertiser, AdvertisingOrder, Act
from billing.models import Invoice
from .services import ensure_ad_post_for_order


def catalog(request):
    """Публичный каталог каналов для рекламы."""
    from channels.models import Channel
    from django.contrib.auth import get_user_model
    User = get_user_model()
    owner_ids = list(User.objects.filter(role=User.ROLE_OWNER).values_list('id', flat=True))
    channels = Channel.objects.filter(is_active=True, ad_enabled=True, owner_id__in=owner_ids).order_by('-subscribers_count')
    return render(request, 'advertisers/catalog.html', {'channels': channels})


def _advertiser_register_next(request):
    if request.method == 'POST':
        return (request.POST.get('next') or '').strip()
    return (request.GET.get('next') or '').strip()


_REG_PREFILL_KEYS = (
    'email', 'password1', 'password2', 'first_name', 'phone', 'company_name', 'inn',
    'legal_address', 'contact_person', 'contact_phone', 'kpp', 'ogrn',
)


def _advertiser_register_context(request, next_redirect=None):
    ctx = {'next_redirect': next_redirect if next_redirect is not None else _advertiser_register_next(request)}
    prefill = {k: '' for k in _REG_PREFILL_KEYS}
    if request.method == 'POST':
        for k in _REG_PREFILL_KEYS:
            prefill[k] = (request.POST.get(k) or '').strip()
        prefill['password1'] = ''
        prefill['password2'] = ''
    ctx['prefill'] = prefill
    return ctx


def advertiser_register(request):
    """Регистрация профиля рекламодателя."""
    if request.user.is_authenticated and hasattr(request.user, 'advertiser_profile'):
        return redirect('advertisers:dashboard')

    if request.method == 'POST':
        next_redirect = (request.POST.get('next') or '').strip()
        next_url = next_redirect
        # Если пользователь не залогинен — создаём аккаунт рекламодателя сразу здесь,
        # чтобы путь /advertisers/order/new/ не упирался в 404.
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
            if User.objects.filter(email=email).exists():
                messages.error(request, 'Пользователь с таким email уже существует. Войдите в аккаунт.')
                return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))

            user = User(
                email=email,
                username=email,
                first_name=first_name or email.split('@')[0],
                phone=phone,
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

        company_name = request.POST.get('company_name', '').strip()
        inn = request.POST.get('inn', '').strip()
        legal_address = request.POST.get('legal_address', '').strip()
        contact_person = request.POST.get('contact_person', '').strip()

        if not all([company_name, inn, legal_address, contact_person]):
            messages.error(request, 'Заполните все обязательные поля.')
            return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))
        if not inn.isdigit() or len(inn) not in (10, 12):
            messages.error(request, 'ИНН должен состоять из 10 или 12 цифр.')
            return render(request, 'advertisers/register.html', _advertiser_register_context(request, next_redirect))
        Advertiser.objects.create(
            user=request.user,
            company_name=company_name,
            inn=inn,
            kpp=request.POST.get('kpp', '').strip(),
            ogrn=request.POST.get('ogrn', '').strip(),
            legal_address=legal_address,
            contact_person=contact_person,
            contact_phone=request.POST.get('contact_phone', '').strip(),
        )
        if getattr(request.user, 'role', '') != request.user.ROLE_ADVERTISER:
            request.user.role = request.user.ROLE_ADVERTISER
            request.user.save(update_fields=['role'])
        messages.success(request, 'Профиль рекламодателя создан.')
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
        return redirect('advertisers:dashboard')

    return render(request, 'advertisers/register.html', _advertiser_register_context(request))


@login_required
def advertiser_dashboard(request):
    try:
        adv = Advertiser.objects.get(user=request.user)
    except Advertiser.DoesNotExist:
        messages.info(request, 'Чтобы создавать заявки на рекламу, заполните профиль рекламодателя.')
        return redirect('advertisers:register')
    orders = AdvertisingOrder.objects.filter(advertiser=adv).select_related('invoice').order_by('-created_at')
    adv = Advertiser.objects.select_related('user').prefetch_related('ord_contracts').get(pk=adv.pk)
    orders_total_count = orders.count()
    active_orders_count = orders.filter(
        status__in=[AdvertisingOrder.STATUS_APPROVED, AdvertisingOrder.STATUS_ACTIVE]
    ).count()
    return render(request, 'advertisers/dashboard.html', {
        'advertiser': adv,
        'orders': orders,
        'orders_total_count': orders_total_count,
        'active_orders_count': active_orders_count,
    })


@login_required
def order_create(request):
    try:
        adv = Advertiser.objects.get(user=request.user)
    except Advertiser.DoesNotExist:
        messages.info(request, 'Сначала заполните профиль рекламодателя (ИНН и название компании).')
        return redirect(f"/advertisers/register/?next={request.path}")
    from channels.models import Channel
    from django.contrib.auth import get_user_model
    User = get_user_model()
    from datetime import date
    from django.utils.dateparse import parse_date

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        start_date_raw = request.POST.get('start_date', '')
        end_date_raw = request.POST.get('end_date', '')
        repeat_interval_days = int(request.POST.get('repeat_interval_days', '0') or 0)
        channel_ids = request.POST.getlist('channels')

        start_date = parse_date(start_date_raw) if start_date_raw else None
        end_date = parse_date(end_date_raw) if end_date_raw else None

        if not all([title, description, start_date, end_date]):
            messages.error(request, 'Заполните все обязательные поля (включая даты).')
        else:
            # Разрешаем выбирать только каналы владельца сервиса
            owner_ids = list(User.objects.filter(role=User.ROLE_OWNER).values_list('id', flat=True))
            allowed_channels = Channel.objects.filter(
                is_active=True, owner_id__in=owner_ids, ad_enabled=True
            )
            if channel_ids:
                channel_ids = list(allowed_channels.filter(pk__in=channel_ids).values_list('pk', flat=True))

            if not channel_ids:
                messages.error(request, 'Выберите хотя бы один канал для размещения.')
                available_channels = allowed_channels
                return render(request, 'advertisers/order_create.html', {'channels': available_channels})

            if end_date < start_date:
                messages.error(request, 'Дата окончания не может быть раньше даты начала.')
                available_channels = allowed_channels
                return render(request, 'advertisers/order_create.html', {'channels': available_channels})

            # Количество размещений
            days = (end_date - start_date).days
            if repeat_interval_days <= 0:
                placements = 1
            else:
                placements = (days // repeat_interval_days) + 1

            # Бюджет = сумма цен каналов * количество размещений
            channels = list(allowed_channels.filter(pk__in=channel_ids))
            per_placement = sum((c.ad_price or 0) for c in channels)
            budget = per_placement * placements

            order = AdvertisingOrder.objects.create(
                advertiser=adv,
                title=title,
                description=description,
                budget=budget,
                start_date=start_date,
                end_date=end_date,
                repeat_interval_days=repeat_interval_days,
                status=AdvertisingOrder.STATUS_SUBMITTED,
            )
            if channel_ids:
                order.channels.set(channel_ids)
            messages.success(request, 'Заявка на рекламу отправлена на рассмотрение.')
            return redirect('advertisers:dashboard')

    # Показываем только активные каналы для размещения
    owner_ids = list(User.objects.filter(role=User.ROLE_OWNER).values_list('id', flat=True))
    available_channels = Channel.objects.filter(is_active=True, owner_id__in=owner_ids, ad_enabled=True)
    return render(request, 'advertisers/order_create.html', {'channels': available_channels})


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
    })


# ──────────────────────────────────────────────────────────────────────────────
# Панель владельца: управление всеми рекламными заявками
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def owner_orders(request):
    """Список всех рекламных заявок (только для owner/staff)."""
    if not (request.user.is_staff or request.user.role == request.user.ROLE_OWNER):
        messages.error(request, 'Нет доступа.')
        return redirect('dashboard')
    status_filter = request.GET.get('status', '')
    orders = AdvertisingOrder.objects.select_related('advertiser').order_by('-created_at')
    if status_filter:
        orders = orders.filter(status=status_filter)
    return render(request, 'advertisers/owner_orders.html', {
        'orders': orders,
        'status_filter': status_filter,
        'statuses': AdvertisingOrder.STATUS_CHOICES,
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
                return redirect('advertisers:owner_orders')
            if order.status != AdvertisingOrder.STATUS_APPROVED:
                messages.error(request, 'Нельзя запустить кампанию из текущего статуса.')
                return redirect('advertisers:owner_orders')
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
                return redirect('advertisers:owner_orders')
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
    return redirect('advertisers:owner_orders')


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
        return redirect('advertisers:owner_orders')

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
    applications = (
        AdApplication.objects.filter(advertiser=adv)
        .select_related('channel', 'channel__owner', 'post', 'invoice')
        .prefetch_related('campaign_posts')
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
        if not app.selected_slot_ids:
            labels.append('слоты')
        post = app.post
        if not post or not (post.text or '').strip():
            labels.append('контент')
        if not app.ord_wizard_saved_at:
            labels.append('ОРД')
        if not app.contract_signed_at:
            labels.append('договор')
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
            'stats_by_channel': list(by_channel),
            'app_issues': app_issues,
        },
    )
