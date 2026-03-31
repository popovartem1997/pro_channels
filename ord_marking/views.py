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
        channel_id = request.POST.get('channel_id')
        advertiser_id = request.POST.get('advertiser_id')
        label_text = request.POST.get('label_text', 'Реклама').strip() or 'Реклама'

        post = get_object_or_404(Post, pk=post_id, author=request.user)
        channel = get_object_or_404(Channel, pk=channel_id, owner=request.user)
        advertiser = None
        if advertiser_id:
            advertiser = Advertiser.objects.filter(pk=advertiser_id).first()

        reg = ORDRegistration.objects.create(
            post=post,
            channel=channel,
            advertiser=advertiser,
            label_text=label_text,
            status=ORDRegistration.STATUS_PENDING,
        )
        _try_register_ord(reg)

        messages.success(request, 'Регистрация ОРД создана.')
        return redirect('ord_marking:list')

    from content.models import Post
    from channels.models import Channel
    from advertisers.models import Advertiser
    posts = Post.objects.filter(author=request.user, status='published').order_by('-created_at')[:100]
    channels = Channel.objects.filter(owner=request.user)
    advertisers = Advertiser.objects.all()
    return render(request, 'ord_marking/create.html', {
        'posts': posts,
        'channels': channels,
        'advertisers': advertisers,
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
