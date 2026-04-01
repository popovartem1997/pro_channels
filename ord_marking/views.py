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
    return render(
        request,
        'ord_marking/detail.html',
        {
            'reg': reg,
            'creative_external_id': ext,
            'sandbox': sandbox,
            'token_set': bool((keys.get_vk_ord_access_token() or '').strip()),
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
