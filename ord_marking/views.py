from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.http import HttpResponseForbidden
from django.utils import timezone

from .models import ORDRegistration
from .services import (
    register_creative_for_registration,
    refresh_erid_from_api,
    submit_statistics_for_month,
    creative_external_id_for,
    load_ord_catalog,
)


def _ord_keys_flags():
    from core.models import get_global_api_keys

    keys = get_global_api_keys()
    return keys, bool(getattr(keys, 'vk_ord_use_sandbox', False))


@login_required
def ord_dashboard(request):
    """Обзор маркировки: статус ключа, ссылки."""
    keys, sandbox = _ord_keys_flags()
    token_set = bool((keys.get_vk_ord_access_token() or '').strip())
    regs = (
        ORDRegistration.objects.filter(post__author=request.user)
        .select_related('post', 'channel', 'advertiser')
        .order_by('-created_at')[:200]
    )
    return render(
        request,
        'ord_marking/dashboard.html',
        {
            'token_set': token_set,
            'sandbox': sandbox,
            'contract_hint': (keys.vk_ord_contract_external_id or '').strip(),
            'pad_hint': (keys.vk_ord_pad_external_id or '').strip(),
            'registrations': regs,
        },
    )


@login_required
@require_POST
def ord_sync_catalog(request):
    """Подтянуть контрагентов/договоры из ЛК ОРД в нашу БД."""
    if not (request.user.is_staff or request.user.is_superuser or getattr(request.user, 'role', '') == 'owner'):
        return HttpResponseForbidden('Forbidden')
    _, sandbox = _ord_keys_flags()
    from advertisers.services import sync_advertisers_and_contracts_from_ord

    try:
        res = sync_advertisers_and_contracts_from_ord(use_sandbox=sandbox)
        if not res.get('ok'):
            messages.error(request, res.get('error') or 'Ошибка синхронизации')
        else:
            messages.success(
                request,
                f"Синхронизация ОРД: рекламодатели +{res.get('created', 0)}, обновлено {res.get('updated', 0)}, договоров {res.get('contracts', 0)}.",
            )
    except Exception as e:
        messages.error(request, f'Ошибка синхронизации ОРД: {e}')
    return redirect('ord_marking:dashboard')


@login_required
def ord_list(request):
    return redirect('ord_marking:dashboard')


@login_required
def ord_create(request):
    from content.models import Post
    from channels.models import Channel
    from advertisers.models import Advertiser

    keys, sandbox = _ord_keys_flags()
    if not (keys.get_vk_ord_access_token() or '').strip():
        messages.warning(
            request,
            'Сначала задайте ключ API ОРД VK (Bearer) в разделе «Ключи API» — поле «Ключ API ОРД VK».',
        )

    if request.method == 'POST':
        post_id = request.POST.get('post_id')
        channel_id = (request.POST.get('channel_id') or '').strip()
        all_post_channels = request.POST.get('all_post_channels') == 'on'
        advertiser_id = request.POST.get('advertiser_id')
        label_text = request.POST.get('label_text', 'Реклама').strip() or 'Реклама'
        contract_override = (request.POST.get('contract_external_id') or '').strip()
        pad_override = (request.POST.get('pad_external_id') or '').strip()
        person_override = (request.POST.get('person_external_id') or '').strip()

        post = get_object_or_404(
            Post.objects.prefetch_related('channels'),
            pk=post_id,
            author=request.user,
        )
        post_channel_list = [c for c in post.channels.all() if c.owner_id == request.user.id]

        advertiser = None
        if advertiser_id:
            advertiser = Advertiser.objects.filter(pk=advertiser_id).first()

        if all_post_channels:
            targets = post_channel_list
            if not targets:
                messages.error(request, 'У поста нет привязанных каналов с вашим аккаунтом.')
                return redirect('ord_marking:create')
        else:
            if not channel_id.isdigit():
                messages.error(request, 'Выберите канал.')
                return redirect('ord_marking:create')
            channel = get_object_or_404(Channel, pk=int(channel_id), owner=request.user)
            if channel not in post_channel_list:
                messages.error(
                    request,
                    'Выбранный канал не входит в этот пост. Сначала опубликуйте пост в нужный канал или выберите другой пост.',
                )
                return redirect('ord_marking:create')
            targets = [channel]

        created = 0
        for ch in targets:
            if ORDRegistration.objects.filter(post=post, channel=ch).exists():
                continue
            reg = ORDRegistration.objects.create(
                post=post,
                channel=ch,
                advertiser=advertiser,
                label_text=label_text,
                status=ORDRegistration.STATUS_PENDING,
                contract_external_id=contract_override,
                pad_external_id=pad_override,
                person_external_id=person_override,
            )
            register_creative_for_registration(reg, use_sandbox=sandbox)
            created += 1

        if created:
            messages.success(request, f'Создано регистраций: {created}. Проверьте ERID в списке.')
        else:
            messages.info(request, 'Для выбранных каналов регистрации уже есть — откройте карточку для повтора.')
        return redirect('ord_marking:dashboard')

    posts = (
        Post.objects.filter(author=request.user, status=Post.STATUS_PUBLISHED)
        .prefetch_related('channels')
        .order_by('-created_at')[:100]
    )
    post_channels_map = {}
    for p in posts:
        post_channels_map[str(p.pk)] = [
            {
                'id': c.pk,
                'name': c.name,
                'platform': c.get_platform_display(),
                'code': c.platform,
            }
            for c in p.channels.all().order_by('name')
            if c.owner_id == request.user.id
        ]
    channels = Channel.objects.filter(owner=request.user).order_by('name')
    advertisers = Advertiser.objects.all().order_by('company_name')
    keys, sandbox = _ord_keys_flags()
    bearer = (keys.get_vk_ord_access_token() or '').strip()
    catalog = load_ord_catalog(bearer, use_sandbox=sandbox) if bearer else {}
    return render(
        request,
        'ord_marking/create.html',
        {
            'posts': posts,
            'channels': channels,
            'advertisers': advertisers,
            'post_channels_map': post_channels_map,
            'default_contract': (keys.vk_ord_contract_external_id or '').strip(),
            'default_pad': (keys.vk_ord_pad_external_id or '').strip(),
            'sandbox': sandbox,
            'token_set': bool(bearer),
            **catalog,
        },
    )


@login_required
def ord_edit(request, pk):
    """Изменить договор / контрагента / площадку и при необходимости снова отправить креатив в ОРД."""
    from advertisers.models import Advertiser

    reg = get_object_or_404(
        ORDRegistration.objects.select_related('post', 'channel', 'advertiser'),
        pk=pk,
        post__author=request.user,
    )
    keys, sandbox = _ord_keys_flags()
    bearer = (keys.get_vk_ord_access_token() or '').strip()
    catalog = load_ord_catalog(bearer, use_sandbox=sandbox) if bearer else {}

    if request.method == 'POST':
        reg.contract_external_id = (request.POST.get('contract_external_id') or '').strip()
        reg.person_external_id = (request.POST.get('person_external_id') or '').strip()
        reg.pad_external_id = (request.POST.get('pad_external_id') or '').strip()
        reg.label_text = (request.POST.get('label_text') or reg.label_text or 'Реклама').strip() or 'Реклама'
        aid = (request.POST.get('advertiser_id') or '').strip()
        if aid.isdigit():
            reg.advertiser = Advertiser.objects.filter(pk=int(aid)).first()
        else:
            reg.advertiser = None
        reg.save()

        if request.POST.get('reregister') == 'on':
            if not bearer:
                messages.error(request, 'Нет ключа API ОРД — повторная регистрация невозможна.')
            else:
                register_creative_for_registration(reg, use_sandbox=sandbox)
                if reg.status == ORDRegistration.STATUS_REGISTERED:
                    messages.success(request, f'Креатив обновлён в ОРД. ERID: {reg.erid or reg.ord_token}')
                else:
                    messages.error(request, reg.error_message or 'Ошибка регистрации в ОРД')
        else:
            messages.success(request, 'Параметры маркировки сохранены.')
        return redirect('ord_marking:detail', pk=pk)

    advertisers = Advertiser.objects.all().order_by('company_name')
    return render(
        request,
        'ord_marking/edit.html',
        {
            'reg': reg,
            'sandbox': sandbox,
            'token_set': bool(bearer),
            'default_contract': (keys.vk_ord_contract_external_id or '').strip(),
            'default_pad': (keys.vk_ord_pad_external_id or '').strip(),
            'advertisers': advertisers,
            **catalog,
        },
    )


@login_required
def ord_detail(request, pk):
    reg = get_object_or_404(
        ORDRegistration.objects.select_related('post', 'channel', 'advertiser'),
        pk=pk,
        post__author=request.user,
    )
    keys, sandbox = _ord_keys_flags()
    ext = (reg.creative_external_id or '').strip() or creative_external_id_for(reg.post_id, reg.channel_id)
    person_eff = (reg.person_external_id or '').strip()
    if not person_eff and reg.advertiser_id:
        from advertisers.models import Advertiser

        try:
            adv = Advertiser.objects.get(pk=reg.advertiser_id)
            person_eff = (adv.ord_person_external_id or '').strip()
        except Advertiser.DoesNotExist:
            pass
    contract_eff = (reg.contract_external_id or '').strip() or (keys.vk_ord_contract_external_id or '').strip()
    pad_eff = (
        (reg.pad_external_id or '').strip()
        or (reg.channel.ord_pad_external_id or '').strip()
        or (keys.vk_ord_pad_external_id or '').strip()
    )
    return render(
        request,
        'ord_marking/detail.html',
        {
            'reg': reg,
            'creative_external_id': ext,
            'sandbox': sandbox,
            'token_set': bool((keys.get_vk_ord_access_token() or '').strip()),
            'person_effective': person_eff,
            'contract_effective': contract_eff,
            'pad_effective': pad_eff,
        },
    )


@login_required
@require_POST
def ord_retry(request, pk):
    reg = get_object_or_404(ORDRegistration, pk=pk, post__author=request.user)
    _, sandbox = _ord_keys_flags()
    register_creative_for_registration(reg, use_sandbox=sandbox)
    if reg.status == ORDRegistration.STATUS_REGISTERED:
        messages.success(request, f'Готово. ERID: {reg.erid or reg.ord_token}')
    else:
        messages.error(request, reg.error_message or 'Ошибка регистрации')
    return redirect('ord_marking:detail', pk=pk)


@login_required
@require_POST
def ord_refresh_erid(request, pk):
    reg = get_object_or_404(ORDRegistration, pk=pk, post__author=request.user)
    _, sandbox = _ord_keys_flags()
    refresh_erid_from_api(reg, use_sandbox=sandbox)
    messages.info(request, 'Данные обновлены с ОРД (если креатив уже в реестре).')
    return redirect('ord_marking:detail', pk=pk)


@login_required
@require_POST
def ord_submit_stats(request, pk):
    reg = get_object_or_404(ORDRegistration, pk=pk, post__author=request.user)
    _, sandbox = _ord_keys_flags()
    try:
        y = int((request.POST.get('year') or timezone.now().year))
        m = int((request.POST.get('month') or timezone.now().month))
    except ValueError:
        messages.error(request, 'Некорректный месяц/год.')
        return redirect('ord_marking:detail', pk=pk)
    ok, msg = submit_statistics_for_month(reg, y, m, use_sandbox=sandbox)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect('ord_marking:detail', pk=pk)


@login_required
def ord_copy_snippet(request, pk):
    """Текст для вставки erid: в пост (plain text)."""
    reg = get_object_or_404(ORDRegistration, pk=pk, post__author=request.user)
    er = (reg.erid or reg.ord_token or '').strip()
    if not er:
        return HttpResponseForbidden('Нет ERID')
    from django.http import HttpResponse

    return HttpResponse(
        f'erid:{er}\n',
        content_type='text/plain; charset=utf-8',
        headers={'Content-Disposition': f'inline; filename="erid-post-{pk}.txt"'},
    )
