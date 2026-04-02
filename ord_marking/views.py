import secrets

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.http import HttpResponseForbidden
from django.http import JsonResponse
from django.utils import timezone

from .models import ORDRegistration, OrdSyncRun
from . import vk_ord_client
from .services import (
    allocate_next_ord_contract_external_id,
    peek_next_ord_contract_external_id,
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


def _can_sync_ord(user) -> bool:
    return bool(user.is_staff or user.is_superuser or getattr(user, 'role', '') == 'owner')


@login_required
@require_POST
def ord_sync_start(request):
    """Запуск синхронизации ОРД в фоне (Celery). Возвращает JSON с id запуска."""
    if not _can_sync_ord(request.user):
        return JsonResponse({'ok': False, 'error': 'Forbidden'}, status=403)
    run = OrdSyncRun.objects.create(created_by=request.user, status=OrdSyncRun.STATUS_PENDING)
    try:
        from ord_marking.tasks import sync_ord_catalog_task

        sync_ord_catalog_task.delay(run.pk)
    except Exception as e:
        run.status = OrdSyncRun.STATUS_ERROR
        run.error_message = str(e)[:2000]
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'finished_at'])
        return JsonResponse({'ok': False, 'error': f'Не удалось запустить задачу: {e}'}, status=500)
    return JsonResponse({'ok': True, 'run_id': run.pk})


@login_required
def ord_sync_status(request, pk: int):
    """Статус фоновой синхронизации ОРД."""
    if not _can_sync_ord(request.user):
        return JsonResponse({'ok': False, 'error': 'Forbidden'}, status=403)
    run = get_object_or_404(OrdSyncRun, pk=pk)
    return JsonResponse(
        {
            'ok': True,
            'status': run.status,
            'error_message': run.error_message,
            'result': run.result,
            'started_at': run.started_at.isoformat() if run.started_at else None,
            'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        }
    )


@login_required
def ord_list(request):
    return redirect('ord_marking:dashboard')


@login_required
def ord_create(request):
    from django.db import transaction
    from django.db.models import Max

    from content.models import Post, PostMedia, normalize_post_media_orders
    from channels.models import Channel
    from advertisers.models import Advertiser

    keys, sandbox = _ord_keys_flags()
    bearer = (keys.get_vk_ord_access_token() or '').strip()
    if not bearer:
        messages.warning(
            request,
            'Сначала задайте ключ API ОРД VK (Bearer) в разделе «Ключи API» — поле «Ключ API ОРД VK».',
        )

    if request.method == 'POST':
        if request.POST.get('form') == 'quick_advertiser':
            User = get_user_model()
            company_name = (request.POST.get('qa_company_name') or '').strip()
            inn = (request.POST.get('qa_inn') or '').strip()
            legal_address = (request.POST.get('qa_legal_address') or '').strip()
            contact_person = (request.POST.get('qa_contact_person') or '').strip()
            email = (request.POST.get('qa_email') or '').strip().lower()
            contact_phone = (request.POST.get('qa_contact_phone') or '').strip()
            if not all([company_name, inn, legal_address, contact_person, email]) or '@' not in email:
                messages.error(
                    request,
                    'Заполните название, ИНН, юр. адрес, контактное лицо и корректный email рекламодателя.',
                )
                return redirect('ord_marking:create')
            if not inn.isdigit() or len(inn) not in (10, 12):
                messages.error(request, 'ИНН должен состоять из 10 или 12 цифр.')
                return redirect('ord_marking:create')
            if User.objects.filter(email=email).exists():
                messages.error(
                    request,
                    'Пользователь с таким email уже есть. Укажите другой email или выберите рекламодателя в списке.',
                )
                return redirect('ord_marking:create')
            pw = secrets.token_urlsafe(14)
            user = User(
                email=email,
                username=email,
                first_name=(contact_person[:150] if contact_person else email.split('@')[0]),
                role=User.ROLE_ADVERTISER,
            )
            user.set_password(pw)
            user.save()
            adv = Advertiser.objects.create(
                user=user,
                company_name=company_name,
                inn=inn,
                legal_address=legal_address,
                contact_person=contact_person,
                contact_phone=contact_phone,
            )
            messages.success(
                request,
                f'Рекламодатель «{company_name}» создан. Временный пароль для входа: {pw} '
                '(сохраните в надёжном месте; пользователь может сменить пароль после входа).',
            )
            return redirect(f'{reverse("ord_marking:create")}?advertiser_id={adv.pk}')

        if request.POST.get('form') == 'create_contract':
            if not bearer:
                messages.error(request, 'Нет ключа API ОРД — создание договора недоступно.')
                return redirect('ord_marking:create')
            ext = allocate_next_ord_contract_external_id()
            client_eid = (request.POST.get('contract_client_external_id') or '').strip()
            contractor_eid = (request.POST.get('contract_contractor_external_id') or '').strip()
            subject = (request.POST.get('contract_subject') or '').strip()
            date_s = (request.POST.get('contract_date') or '').strip()
            if not client_eid or not subject or not date_s:
                messages.error(
                    request,
                    'Укажите клиента (person), предмет договора и дату.',
                )
                return redirect('ord_marking:create')
            body: dict = {
                'client_external_id': client_eid,
                'subject': subject[:2000],
                'date': date_s,
            }
            if contractor_eid:
                body['contractor_external_id'] = contractor_eid
            try:
                vk_ord_client.put_contract_v1(bearer, ext, body, use_sandbox=sandbox)
                messages.success(
                    request,
                    f'Договор «{ext}» создан/обновлён в ОРД. Выберите его в подсказках или введите id вручную.',
                )
            except vk_ord_client.OrdVkApiError as e:
                messages.error(request, str(e))
            except Exception as e:
                messages.error(request, str(e)[:2000])
            return redirect('ord_marking:create')

        source_mode = (request.POST.get('source_mode') or 'published_post').strip()
        all_post_channels = source_mode == 'published_post' and request.POST.get('all_post_channels') == 'on'
        advertiser_id = request.POST.get('advertiser_id')
        label_text = request.POST.get('label_text', 'Реклама').strip() or 'Реклама'
        contract_override = (request.POST.get('contract_external_id') or '').strip()
        pad_override = (request.POST.get('pad_external_id') or '').strip()
        person_override = (request.POST.get('person_external_id') or '').strip()

        post = None
        post_channel_list = []

        if source_mode == 'standalone':
            standalone_text = (request.POST.get('standalone_text') or '').strip()
            ch_raw = (request.POST.get('standalone_channel_id') or '').strip()
            if not standalone_text:
                messages.error(request, 'Введите текст креатива для маркировки.')
                return redirect('ord_marking:create')
            if not ch_raw.isdigit():
                messages.error(request, 'Выберите канал, с которым связана реклама в соцсети.')
                return redirect('ord_marking:create')
            channel_one = get_object_or_404(Channel, pk=int(ch_raw), owner=request.user)
            with transaction.atomic():
                post = Post.objects.create(
                    author=request.user,
                    text=standalone_text,
                    text_html=(request.POST.get('standalone_text_html') or '').strip()[:120_000],
                    status=Post.STATUS_DRAFT,
                )
                post.channels.set([channel_one])
                base = PostMedia.objects.filter(post=post).aggregate(m=Max('order'))['m']
                base_i = int(base) if base is not None else 0
                for idx, f in enumerate(request.FILES.getlist('standalone_media')):
                    media_type = PostMedia.TYPE_PHOTO
                    if f.content_type.startswith('video'):
                        media_type = PostMedia.TYPE_VIDEO
                    elif not f.content_type.startswith('image'):
                        media_type = PostMedia.TYPE_DOCUMENT
                    PostMedia.objects.create(
                        post=post,
                        file=f,
                        media_type=media_type,
                        order=base_i + idx + 1,
                    )
                normalize_post_media_orders(post)
            post_channel_list = [channel_one]
            channel_id = ch_raw
        else:
            post_id = (request.POST.get('post_id') or '').strip()
            channel_id = (request.POST.get('channel_id') or '').strip()
            if not post_id.isdigit():
                messages.error(request, 'Выберите опубликованный пост.')
                return redirect('ord_marking:create')
            post = get_object_or_404(
                Post.objects.prefetch_related('channels').filter(status=Post.STATUS_PUBLISHED),
                pk=int(post_id),
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
                    'Выбранный канал не подходит к этому посту. Для режима «с сайта» выберите канал из поста.',
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
    catalog = load_ord_catalog(bearer, use_sandbox=sandbox)
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
            'suggested_contract_external_id': peek_next_ord_contract_external_id(),
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
    catalog = load_ord_catalog(bearer, use_sandbox=sandbox)

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
