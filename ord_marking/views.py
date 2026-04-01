from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .models import ORDRegistration


@login_required
def ord_list(request):
    registrations = ORDRegistration.objects.filter(
        post__author=request.user
    ).select_related('post', 'channel', 'advertiser').order_by('-created_at')
    return render(request, 'ord_marking/list.html', {'registrations': registrations})


@login_required
def ord_create(request):
    from content.models import Post
    from channels.models import Channel
    from advertisers.models import Advertiser

    if request.method == 'POST':
        post_id = request.POST.get('post_id')
        channel_id = (request.POST.get('channel_id') or '').strip()
        all_post_channels = request.POST.get('all_post_channels') == 'on'
        advertiser_id = request.POST.get('advertiser_id')
        label_text = request.POST.get('label_text', 'Реклама').strip() or 'Реклама'

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
                messages.error(request, 'Выбранный канал не входит в этот пост. Сначала опубликуйте пост в нужный канал или выберите другой пост.')
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
            )
            _try_register_ord(reg)
            created += 1

        if created:
            messages.success(request, f'Создано регистраций ОРД: {created}.')
        else:
            messages.info(request, 'Для выбранных каналов регистрации уже существуют.')
        return redirect('ord_marking:list')

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
    advertisers = Advertiser.objects.all()
    return render(request, 'ord_marking/create.html', {
        'posts': posts,
        'channels': channels,
        'advertisers': advertisers,
        'post_channels_map': post_channels_map,
    })


def _try_register_ord(reg):
    """Попытка регистрации в ВК ОРД API."""
    from core.models import get_global_api_keys
    keys = get_global_api_keys()
    access_token = (keys.get_vk_ord_access_token() or '').strip()
    if not access_token:
        raise ValueError('VK_ORD_ACCESS_TOKEN не задан (Ключи API → VK ORD).')
    try:
        import requests as req
        response = req.post(
            'https://api.vk.com/method/ads.createAdLabel',
            data={'access_token': access_token, 'v': '5.131', 'name': reg.label_text},
            timeout=10,
        )
        data = response.json()
        reg.raw_response = data
        resp = data.get('response')
        if resp:
            reg.ord_id = str(resp.get('id', ''))
            reg.ord_token = resp.get('token', '')
            reg.status = ORDRegistration.STATUS_REGISTERED
            reg.registered_at = timezone.now()
        else:
            reg.status = ORDRegistration.STATUS_ERROR
            reg.error_message = str(data.get('error', 'Неизвестная ошибка'))
        reg.save()
    except Exception as e:
        reg.status = ORDRegistration.STATUS_ERROR
        reg.error_message = str(e)
        reg.save()
