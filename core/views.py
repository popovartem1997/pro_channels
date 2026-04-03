from urllib.parse import urlencode

from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Prefetch
from django.urls import reverse
from django.utils import timezone
from django.core.paginator import Paginator


def health_live(request):
    """Проверка живости приложения (без БД). Для мониторинга с корректным Host в ALLOWED_HOSTS."""
    return HttpResponse('ok', content_type='text/plain')


def home(request):
    # Если пользователь уже вошёл — показываем кабинет, а не лендинг
    if getattr(request, "user", None) and request.user.is_authenticated:
        if getattr(request.user, "role", "") == "advertiser":
            return redirect("advertisers:campaign_list")
        return redirect("dashboard")
    return render(request, "home.html")


def robots_txt(request):
    base = getattr(request, "build_absolute_uri", lambda p: p)("/").rstrip("/")
    content = """User-agent: *
Allow: /
Disallow: /admin/
Disallow: /dashboard/
Disallow: /profile/
Disallow: /billing/
Disallow: /api/

Sitemap: {site_url}/sitemap.xml
""".format(site_url=base)
    return HttpResponse(content, content_type='text/plain')


def offer(request):
    return render(request, 'core/offer.html')


def privacy(request):
    return render(request, 'core/privacy.html')


def quickstart(request):
    """Быстрый старт по сервису (в т.ч. для SEO и onboarding)."""
    # По требованиям: быстрый старт видит только суперпользователь.
    if getattr(request, "user", None) and request.user.is_authenticated:
        if request.user.is_superuser:
            return render(request, 'core/quickstart.html')
        return redirect('dashboard')
    return render(request, 'core/quickstart.html')


@login_required
def api_keys(request):
    """Глобальные ключи сервиса (редактировать могут только staff/superuser)."""
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponse(status=403)

    from .models import get_global_api_keys
    from .forms import GlobalApiKeysForm

    obj = get_global_api_keys()
    if request.method == 'POST':
        form = GlobalApiKeysForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Ключи сохранены.')
            return redirect('core:api_keys')
        messages.error(request, 'Проверьте поля формы.')
    else:
        form = GlobalApiKeysForm(instance=obj)

    return render(request, 'core/api_keys.html', {'form': form, 'obj': obj})


@login_required
def audit_log(request):
    """Журнал действий и посещений (доступ: staff/superuser/owner)."""
    if not (request.user.is_staff or request.user.is_superuser or getattr(request.user, 'role', '') == 'owner'):
        return HttpResponse(status=403)
    from bots.models import AuditLog
    from core.models import PageVisit

    # ---- Filters (GET) ----
    q_action = (request.GET.get('action') or '').strip()
    q_actor = (request.GET.get('actor') or '').strip()
    q_object_type = (request.GET.get('object_type') or '').strip()
    q_path = (request.GET.get('path') or '').strip()
    q_ip = (request.GET.get('ip') or '').strip()
    q_from = (request.GET.get('from') or '').strip()
    q_to = (request.GET.get('to') or '').strip()

    audit_qs = AuditLog.objects.select_related('actor', 'owner').all()
    visits_qs = PageVisit.objects.select_related('user').all()

    if q_action:
        audit_qs = audit_qs.filter(action=q_action)
    if q_actor:
        audit_qs = audit_qs.filter(actor__username=q_actor)
        # Тот же логин — в посещениях страниц (поле user, не actor)
        visits_qs = visits_qs.filter(user__username=q_actor)
    if q_object_type:
        audit_qs = audit_qs.filter(object_type=q_object_type)

    if q_path:
        visits_qs = visits_qs.filter(path=q_path)
    if q_ip:
        visits_qs = visits_qs.filter(ip=q_ip)

    # Date range (created_at)
    def _parse_dt(val: str):
        try:
            # Accept YYYY-MM-DD or YYYY-MM-DDTHH:MM
            from django.utils.dateparse import parse_datetime, parse_date
            if 'T' in val:
                dt = parse_datetime(val)
                if dt:
                    return dt
            d = parse_date(val)
            if d:
                return timezone.make_aware(timezone.datetime(d.year, d.month, d.day))
        except Exception:
            return None
        return None

    dt_from = _parse_dt(q_from) if q_from else None
    dt_to = _parse_dt(q_to) if q_to else None
    if dt_from:
        audit_qs = audit_qs.filter(created_at__gte=dt_from)
        visits_qs = visits_qs.filter(created_at__gte=dt_from)
    if dt_to:
        audit_qs = audit_qs.filter(created_at__lte=dt_to)
        visits_qs = visits_qs.filter(created_at__lte=dt_to)

    # Choices for dropdown filters (take from recent history to keep short)
    recent_audit = AuditLog.objects.order_by('-created_at')
    action_choices = list(recent_audit.values_list('action', flat=True).distinct().order_by('action')[:300])
    actor_choices = list(
        recent_audit.values_list('actor__username', flat=True)
        .exclude(actor__isnull=True)
        .exclude(actor__username__isnull=True)
        .exclude(actor__username='')
        .distinct()
        .order_by('actor__username')[:300]
    )
    visit_usernames = list(
        PageVisit.objects.values_list('user__username', flat=True)
        .exclude(user__isnull=True)
        .exclude(user__username__isnull=True)
        .exclude(user__username='')
        .distinct()
        .order_by('user__username')[:300]
    )
    actor_choices = sorted(set(actor_choices) | set(visit_usernames), key=str.lower)
    object_type_choices = list(
        recent_audit.values_list('object_type', flat=True)
        .exclude(object_type__isnull=True)
        .exclude(object_type='')
        .distinct()
        .order_by('object_type')[:200]
    )

    recent_visits = PageVisit.objects.order_by('-created_at')
    path_choices = list(
        recent_visits.values_list('path', flat=True)
        .exclude(path__isnull=True)
        .exclude(path='')
        .distinct()
        .order_by('path')[:300]
    )
    ip_choices = list(
        recent_visits.values_list('ip', flat=True)
        .exclude(ip__isnull=True)
        .exclude(ip='')
        .distinct()
        .order_by('ip')[:200]
    )

    audit_qs = audit_qs.order_by('-created_at')
    visits_qs = visits_qs.order_by('-created_at')

    try:
        audit_per_page = int((request.GET.get('audit_per_page') or '').strip() or 40)
    except Exception:
        audit_per_page = 40
    audit_per_page = max(10, min(audit_per_page, 200))
    try:
        visit_per_page = int((request.GET.get('visit_per_page') or '').strip() or 40)
    except Exception:
        visit_per_page = 40
    visit_per_page = max(10, min(visit_per_page, 200))

    audit_page = (request.GET.get('audit_page') or '').strip() or '1'
    visit_page = (request.GET.get('visit_page') or '').strip() or '1'
    audit_paginator = Paginator(audit_qs, audit_per_page)
    visit_paginator = Paginator(visits_qs, visit_per_page)
    audit_page_obj = audit_paginator.get_page(audit_page)
    visit_page_obj = visit_paginator.get_page(visit_page)
    audit = list(audit_page_obj.object_list)
    visits = list(visit_page_obj.object_list)

    # Enrich page visits with resolved route name (best effort)
    from django.urls import resolve, Resolver404

    def _resolve_name(path: str) -> str:
        try:
            match = resolve(path)
            if match and match.view_name:
                return match.view_name
        except Resolver404:
            return ''
        except Exception:
            return ''
        return ''

    def _view_label(view_name: str) -> str:
        """
        Читабельное название для view_name (best-effort).
        Пример: 'channels:detail' -> 'channels → detail'
        """
        s = (view_name or '').strip()
        if not s:
            return ''
        return s.replace(':', ' → ').replace('_', ' ')

    visits_enriched = []
    for v in visits:
        vn = _resolve_name(v.path)
        visits_enriched.append({
            'obj': v,
            'view_name': vn,
            'view_label': _view_label(vn),
        })

    def _audit_base_qs():
        q = {}
        for k, v in request.GET.items():
            if k in ('audit_page', 'visit_page', 'audit_per_page', 'visit_per_page'):
                continue
            if v is None or str(v).strip() == '':
                continue
            q[k] = v
        return urlencode(q)

    audit_base_qs = _audit_base_qs()

    return render(request, 'core/audit_log.html', {
        'audit': audit,
        'visits_enriched': visits_enriched,
        'action_choices': action_choices,
        'actor_choices': actor_choices,
        'object_type_choices': object_type_choices,
        'path_choices': path_choices,
        'ip_choices': ip_choices,
        'audit_page_obj': audit_page_obj,
        'audit_paginator': audit_paginator,
        'visit_page_obj': visit_page_obj,
        'visit_paginator': visit_paginator,
        'audit_per_page': audit_per_page,
        'visit_per_page': visit_per_page,
        'audit_base_qs': audit_base_qs,
        'filters': {
            'action': q_action,
            'actor': q_actor,
            'object_type': q_object_type,
            'path': q_path,
            'ip': q_ip,
            'from': q_from,
            'to': q_to,
        }
    })


def _feed_scoped_querysets(user, *, channel_id=None, chgroup_id=None):
    """
    Базовые queryset'ы ленты (роль, фильтр канала, группа каналов) без kind/status.
    channel_id / chgroup_id — int или None.
    """
    from content.models import Post
    from bots.models import Suggestion
    from channels.models import Channel, ChannelGroup
    from parsing.models import ParsedItem

    if getattr(user, 'role', '') in ('manager', 'assistant_admin') and not (user.is_staff or user.is_superuser):
        from managers.models import TeamMember

        allowed_channel_ids = TeamMember.objects.filter(
            member=user,
            is_active=True,
        ).values_list('channels__pk', flat=True)
        allowed_channel_ids = list(set(int(x) for x in allowed_channel_ids if str(x).isdigit()))
        allowed_channels = list(Channel.objects.filter(pk__in=allowed_channel_ids, is_active=True).order_by('name'))
        post_qs = Post.objects.filter(channels__pk__in=allowed_channel_ids).distinct()
        sug_qs = Suggestion.objects.filter(
            bot__channel_groups__channels__pk__in=allowed_channel_ids
        ).distinct()
        parsed_qs = ParsedItem.objects.filter(
            Q(source__channel_id__in=allowed_channel_ids) | Q(keyword__channel_id__in=allowed_channel_ids)
        ).distinct()
    else:
        if user.is_staff or user.is_superuser:
            allowed_channels = list(Channel.objects.filter(is_active=True).order_by('name'))
        else:
            allowed_channels = list(Channel.objects.filter(owner=user, is_active=True).order_by('name'))
        post_qs = Post.objects.filter(author=user) if not (user.is_staff or user.is_superuser) else Post.objects.all()
        sug_qs = Suggestion.objects.filter(bot__owner=user) if not (user.is_staff or user.is_superuser) else Suggestion.objects.all()
        if user.is_staff or user.is_superuser:
            parsed_qs = ParsedItem.objects.all()
        else:
            parsed_qs = ParsedItem.objects.filter(
                Q(source__owner=user) | Q(keyword__owner=user)
            ).distinct()

    if channel_id is not None:
        post_qs = post_qs.filter(channels__pk=channel_id).distinct()
        sug_qs = sug_qs.filter(bot__channel_groups__channels__pk=channel_id).distinct()
        parsed_qs = parsed_qs.filter(Q(source__channel_id=channel_id) | Q(keyword__channel_id=channel_id)).distinct()

    if chgroup_id is not None:
        g = ChannelGroup.objects.filter(pk=chgroup_id).first()
        if g:
            if user.is_staff or user.is_superuser:
                feed_chgroup_cids = list(g.channels.filter(is_active=True).values_list('pk', flat=True))
            else:
                allowed_set = {c.pk for c in (allowed_channels or [])}
                feed_chgroup_cids = [x for x in g.channels.values_list('pk', flat=True) if x in allowed_set]
            post_qs = post_qs.filter(channels__pk__in=feed_chgroup_cids).distinct()
            sug_qs = sug_qs.filter(bot__channel_groups__pk=chgroup_id).distinct()
            parsed_qs = parsed_qs.filter(
                Q(source__channel_id__in=feed_chgroup_cids) | Q(keyword__channel_id__in=feed_chgroup_cids)
            ).distinct()

    return post_qs, sug_qs, parsed_qs, allowed_channels


def compute_feed_quick_link_counts(user, *, channel_id=None, chgroup_id=None):
    """Актуальные числа для плашек над лентой (тот же scope, что у страницы)."""
    from content.models import Post
    from bots.models import Suggestion
    from parsing.models import ParsedItem

    post_qs, sug_qs, parsed_qs, _ = _feed_scoped_querysets(
        user, channel_id=channel_id, chgroup_id=chgroup_id
    )
    return {
        'subscriber_pending': sug_qs.filter(status=Suggestion.STATUS_PENDING).count(),
        'post_draft': post_qs.filter(status=Post.STATUS_DRAFT).count(),
        'post_scheduled': post_qs.filter(status=Post.STATUS_SCHEDULED).count(),
        'post_publishing': post_qs.filter(status=Post.STATUS_PUBLISHING).count(),
        'post_failed': post_qs.filter(status=Post.STATUS_FAILED).count(),
        'parsing_new': parsed_qs.filter(status=ParsedItem.STATUS_NEW).count(),
    }


@login_required
def feed(request):
    """
    Единая лента: посты + предложка (от подписчиков) + парсинг.
    Адаптирована под моб/десктоп, с фильтрами.
    """
    from content.models import Post
    from bots.models import Suggestion
    from channels.models import Channel
    from parsing.models import ParsedItem

    kind = (request.GET.get('kind') or 'all').strip()  # all|post|subscriber|parsing|ad
    if kind not in ('all', 'post', 'subscriber', 'parsing', 'ad'):
        kind = 'all'
    # unified status filter (one select in UI)
    status_filter = (request.GET.get('status') or '').strip()
    status_kind = ''
    status_value = status_filter
    if ':' in status_filter:
        status_kind, status_value = status_filter.split(':', 1)
        status_kind = (status_kind or '').strip()
        status_value = (status_value or '').strip()
    channel_id = (request.GET.get('channel') or '').strip()

    cid_int = int(channel_id) if (channel_id and str(channel_id).isdigit()) else None

    chgroup_param = (request.GET.get('chgroup') or '').strip()
    chgroup_applied = False
    if chgroup_param.isdigit():
        from channels.models import ChannelGroup

        if ChannelGroup.objects.filter(pk=int(chgroup_param)).exists():
            chgroup_applied = True
    chgroup_int = int(chgroup_param) if chgroup_applied else None

    post_qs, sug_qs, parsed_qs, allowed_channels = _feed_scoped_querysets(
        request.user,
        channel_id=cid_int,
        chgroup_id=chgroup_int,
    )

    post_qs = post_qs.prefetch_related(
        Prefetch('channels', queryset=Channel.objects.select_related('channel_group')),
    ).select_related('author', 'published_by')
    sug_qs = sug_qs.select_related('bot', 'bot__owner').prefetch_related(
        'bot__channel_groups__channels',
    )
    parsed_qs = parsed_qs.select_related(
        'source', 'source__channel', 'source__channel_group', 'keyword', 'keyword__channel',
    )

    if kind == 'ad':
        post_qs = post_qs.filter(campaign_application__isnull=False)

    items = []

    def _groups_for_post(post):
        seen = set()
        out = []
        for ch in post.channels.all():
            g = getattr(ch, 'channel_group', None)
            if g and g.pk not in seen:
                seen.add(g.pk)
                out.append(g)
        return out

    def _feed_qs(**params):
        q = {k: v for k, v in params.items() if v is not None and v != ''}
        if cid_int:
            q['channel'] = str(cid_int)
        if chgroup_int:
            q['chgroup'] = str(chgroup_int)
        return reverse('core:feed') + '?' + urlencode(q)

    feed_quick_links = []
    n_mod = sug_qs.filter(status=Suggestion.STATUS_PENDING).count()
    if n_mod:
        feed_quick_links.append(
            {
                'key': 'subscriber_pending',
                'label': f'На модерации · {n_mod}',
                'url': _feed_qs(kind='subscriber', status='subscriber:pending'),
            }
        )
    n_draft = post_qs.filter(status=Post.STATUS_DRAFT).count()
    if n_draft:
        feed_quick_links.append(
            {
                'key': 'post_draft',
                'label': f'Черновики · {n_draft}',
                'url': _feed_qs(kind='post', status=f'post:{Post.STATUS_DRAFT}'),
            }
        )
    n_sch = post_qs.filter(status=Post.STATUS_SCHEDULED).count()
    if n_sch:
        feed_quick_links.append(
            {
                'key': 'post_scheduled',
                'label': f'Запланированы · {n_sch}',
                'url': _feed_qs(kind='post', status=f'post:{Post.STATUS_SCHEDULED}'),
            }
        )
    n_pubing = post_qs.filter(status=Post.STATUS_PUBLISHING).count()
    if n_pubing:
        feed_quick_links.append(
            {
                'key': 'post_publishing',
                'label': f'Публикуются · {n_pubing}',
                'url': _feed_qs(kind='post', status=f'post:{Post.STATUS_PUBLISHING}'),
            }
        )
    n_fail = post_qs.filter(status=Post.STATUS_FAILED).count()
    if n_fail:
        feed_quick_links.append(
            {
                'key': 'post_failed',
                'label': f'Ошибки · {n_fail}',
                'url': _feed_qs(kind='post', status=f'post:{Post.STATUS_FAILED}'),
            }
        )
    n_parse = parsed_qs.filter(status=ParsedItem.STATUS_NEW).count()
    if n_parse:
        feed_quick_links.append(
            {
                'key': 'parsing_new',
                'label': f'Парсинг (новые) · {n_parse}',
                'url': _feed_qs(kind='parsing', status='parsing:pending'),
            }
        )

    # Apply unified status filter depending on kind.
    if status_filter:
        # Backward-compat: old unprefixed values from old UI
        if status_kind in ('', None):
            if status_filter in {Post.STATUS_DRAFT, Post.STATUS_SCHEDULED, Post.STATUS_PUBLISHING, Post.STATUS_PUBLISHED, Post.STATUS_FAILED}:
                status_kind, status_value = 'post', status_filter
            elif status_filter in {Suggestion.STATUS_PENDING, Suggestion.STATUS_APPROVED, Suggestion.STATUS_REJECTED, Suggestion.STATUS_PUBLISHED}:
                status_kind, status_value = 'subscriber', status_filter
            elif status_filter in {'pending', 'rejected', 'published'}:
                status_kind, status_value = 'parsing', status_filter

        if kind in ('post', 'ad'):
            v = status_value if status_kind in ('', 'post') else status_filter
            if ':' in v:
                v = v.split(':', 1)[1]
            post_qs = post_qs.filter(status=v)
        elif kind == 'subscriber':
            v = status_value if status_kind in ('', 'subscriber') else status_filter
            if ':' in v:
                v = v.split(':', 1)[1]
            sug_qs = sug_qs.filter(status=v)
        elif kind == 'parsing':
            v = status_value if status_kind in ('', 'parsing') else status_filter
            if ':' in v:
                v = v.split(':', 1)[1]
            if v == 'pending':
                parsed_qs = parsed_qs.filter(status=ParsedItem.STATUS_NEW)
            elif v == 'rejected':
                parsed_qs = parsed_qs.filter(status=ParsedItem.STATUS_IGNORED)
            elif v == 'published':
                parsed_qs = parsed_qs.filter(status=ParsedItem.STATUS_USED)
        else:
            # kind=all: now disambiguate by status_kind
            if status_kind == 'post':
                post_qs = post_qs.filter(status=status_value)
                sug_qs = sug_qs.none()
                parsed_qs = parsed_qs.none()
            elif status_kind == 'subscriber':
                sug_qs = sug_qs.filter(status=status_value)
                post_qs = post_qs.none()
                parsed_qs = parsed_qs.none()
            elif status_kind == 'parsing':
                post_qs = post_qs.none()
                sug_qs = sug_qs.none()
                if status_value == 'pending':
                    parsed_qs = parsed_qs.filter(status=ParsedItem.STATUS_NEW)
                elif status_value == 'rejected':
                    parsed_qs = parsed_qs.filter(status=ParsedItem.STATUS_IGNORED)
                elif status_value == 'published':
                    parsed_qs = parsed_qs.filter(status=ParsedItem.STATUS_USED)

    search_q = (request.GET.get('q') or '').strip()
    if search_q:
        from django.db.models import Q

        post_qs = post_qs.filter(text__icontains=search_q)
        sug_qs = sug_qs.filter(text__icontains=search_q)
        parsed_qs = parsed_qs.filter(
            Q(text__icontains=search_q)
            | Q(original_url__icontains=search_q)
            | Q(source__name__icontains=search_q)
        )

    if kind in ('all', 'post', 'ad'):
        from content.models import PostMedia
        for p in post_qs.order_by('-created_at')[:200]:
            items.append({
                'kind': 'post',
                'dt': p.created_at,
                'title': 'Реклама' if p.campaign_application_id else 'Пост',
                'text': p.text or '',
                'status': p.status,
                'status_display': p.get_status_display(),
                'channels': list(p.channels.all()),
                'channel_groups': _groups_for_post(p),
                'url': f'/posts/{p.pk}/',
                'meta': f'Автор: {getattr(p.author, "username", "—")}' + (f' · Опубликовал: {p.published_by.username}' if getattr(p, 'published_by', None) else ''),
                'obj': p,
                'media': list(PostMedia.objects.filter(post=p).order_by('order', 'pk')[:6]),
            })

    if kind in ('all', 'subscriber'):
        q = sug_qs.order_by('-submitted_at')
        for s in q[:200]:
            items.append({
                'kind': 'subscriber',
                'dt': s.submitted_at,
                'title': 'От подписчика',
                'text': s.text or '',
                'status': s.status,
                'status_display': s.get_status_display(),
                'channels': s.bot.display_channels(),
                'channel_groups': [],
                'url': reverse('bots:suggestion_detail', args=[s.pk]),
                'meta': f'{s.bot.name} · {s.sender_display}',
                'obj': s,
            })

    if kind in ('all', 'parsing'):
        q = parsed_qs.order_by('-found_at')
        from parsing.feed_ai_moods import moods_for_template, workspace_owner_for_parsed_item

        for pi in q[:200]:
            st = 'pending' if pi.status == ParsedItem.STATUS_NEW else ('published' if pi.status == ParsedItem.STATUS_USED else 'rejected')
            st_display = 'Новые' if pi.status == ParsedItem.STATUS_NEW else ('Использованы' if pi.status == ParsedItem.STATUS_USED else 'Пропущены')
            cg = getattr(pi.source, 'channel_group', None)
            pwo = workspace_owner_for_parsed_item(pi)
            items.append({
                'kind': 'parsing',
                'dt': pi.found_at,
                'title': '',
                'text': pi.text or '',
                'status': st,
                'status_display': st_display,
                'channels': [pi.source.channel] if getattr(pi.source, 'channel', None) else [],
                'channel_groups': [cg] if cg else [],
                'url': pi.original_url or '',
                'meta': f'{pi.source.get_platform_display()} · {pi.source.name}',
                'obj': pi,
                'parse_workspace_owner_id': pwo.pk if pwo else None,
                'parse_ai_moods': moods_for_template(pwo),
            })

    items.sort(key=lambda x: x.get('dt') or timezone.now(), reverse=True)
    # Safety cap to avoid building too large list in memory (we paginate below).
    items = items[:1000]

    # Pagination
    try:
        per_page = int((request.GET.get('per_page') or '').strip() or 10)
    except Exception:
        per_page = 10
    per_page = max(10, min(per_page, 200))

    page_number = (request.GET.get('page') or '').strip()
    paginator = Paginator(items, per_page)
    page_obj = paginator.get_page(page_number)

    from django.contrib.auth import get_user_model
    from parsing.feed_ai_moods import can_manage_feed_ai_moods, moods_list_for_owner

    User = get_user_model()
    parse_owner_ids = {
        it.get('parse_workspace_owner_id')
        for it in page_obj.object_list
        if it.get('kind') == 'parsing' and it.get('parse_workspace_owner_id')
    }
    parse_owner_ids.discard(None)
    feed_ai_editor_owner = None
    if len(parse_owner_ids) == 1:
        feed_ai_editor_owner = User.objects.filter(pk=next(iter(parse_owner_ids))).first()
    feed_ai_can_edit_moods = bool(
        feed_ai_editor_owner and can_manage_feed_ai_moods(request.user, feed_ai_editor_owner)
    )
    feed_ai_moods_editor_data = (
        moods_list_for_owner(feed_ai_editor_owner) if feed_ai_editor_owner else []
    )

    # Base querystring without page/per_page (used for pagination links)
    try:
        base_q = {}
        for k, v in request.GET.items():
            if k in ('page', 'per_page'):
                continue
            if v is None or str(v).strip() == '':
                continue
            base_q[k] = v
        page_base_qs = urlencode(base_q)
    except Exception:
        page_base_qs = ''

    return render(request, 'core/feed.html', {
        'items': list(page_obj.object_list),
        'kind': kind,
        'status_filter': status_filter,
        'search_q': search_q,
        'channels': allowed_channels or [],
        'channel_id': cid_int or '',
        'chgroup_id': chgroup_int or '',
        'feed_quick_links': feed_quick_links,
        'page_obj': page_obj,
        'paginator': paginator,
        'per_page': per_page,
        'page_base_qs': page_base_qs,
        'feed_ai_editor_owner': feed_ai_editor_owner,
        'feed_ai_can_edit_moods': feed_ai_can_edit_moods,
        'feed_ai_moods_editor_data': feed_ai_moods_editor_data,
    })


def _user_can_celery_monitor(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    role = getattr(user, 'role', '') or ''
    return role in ('owner', 'manager', 'assistant_admin')


@login_required
def celery_monitor(request):
    """
    Очереди Redis, активные задачи воркера, импорты истории, парсинг, запланированные посты.
    """
    if not _user_can_celery_monitor(request.user):
        return HttpResponse(status=403)

    from channels.models import HistoryImportRun
    from content.models import Post
    from parsing.models import ParseTask

    from .celery_monitor_service import (
        beat_periodic_preview,
        celery_inspect_bundle,
        filter_tasks_by_category,
        filter_tasks_by_name,
        recent_task_results,
        redis_queue_lengths,
        settings_celery_summary,
        support_log_commands,
        parse_support_bundle_from_logs_hint,
        task_category,
    )

    cat = (request.GET.get('cat') or 'all').strip().lower()
    if cat not in ('all', 'publish', 'parse', 'import', 'other'):
        cat = 'all'

    name_sub = (request.GET.get('task_q') or '').strip()

    hist_st = (request.GET.get('hist_status') or 'all').strip().lower()
    if hist_st not in ('all', 'pending', 'running', 'done', 'error', 'cancelled'):
        hist_st = 'all'

    parse_f = (request.GET.get('parse_filter') or 'all').strip().lower()
    if parse_f not in ('all', 'active', 'inactive'):
        parse_f = 'all'

    post_st = (request.GET.get('post_status') or 'scheduled').strip().lower()
    if post_st not in ('scheduled', 'publishing', 'all'):
        post_st = 'scheduled'

    tr_status = (request.GET.get('tr_status') or '').strip().upper()
    if tr_status not in ('', 'SUCCESS', 'FAILURE', 'PENDING', 'STARTED', 'RETRY'):
        tr_status = ''

    broker = (getattr(settings, 'CELERY_BROKER_URL', None) or '').strip()
    queues, q_err = redis_queue_lengths(broker)
    insp = celery_inspect_bundle(timeout=1.0, include_scheduled_stats=False)
    active_raw = dict(insp.get('active') or {})
    reserved_raw = dict(insp.get('reserved') or {})

    active_f = filter_tasks_by_name(filter_tasks_by_category(active_raw, cat), name_sub)
    reserved_f = filter_tasks_by_name(filter_tasks_by_category(reserved_raw, cat), name_sub)

    staff = request.user.is_staff or request.user.is_superuser

    hist_qs = HistoryImportRun.objects.select_related(
        'source_channel', 'target_channel', 'created_by'
    ).order_by('-created_at')
    if not staff:
        hist_qs = hist_qs.filter(created_by=request.user)
    if hist_st != 'all':
        hist_qs = hist_qs.filter(status=hist_st)
    history_runs = list(hist_qs[:80])

    parse_qs = ParseTask.objects.all().order_by('-last_run_at', '-pk')
    if not staff:
        parse_qs = parse_qs.filter(owner=request.user)
    if parse_f == 'active':
        parse_qs = parse_qs.filter(is_active=True)
    elif parse_f == 'inactive':
        parse_qs = parse_qs.filter(is_active=False)
    parse_tasks = list(parse_qs[:80])

    post_qs = Post.objects.select_related('author').order_by('scheduled_at', '-updated_at')
    if not staff:
        post_qs = post_qs.filter(author=request.user)
    if post_st == 'scheduled':
        post_qs = post_qs.filter(status=Post.STATUS_SCHEDULED)
    elif post_st == 'publishing':
        post_qs = post_qs.filter(status=Post.STATUS_PUBLISHING)
    else:
        post_qs = post_qs.filter(
            status__in=(Post.STATUS_SCHEDULED, Post.STATUS_PUBLISHING)
        )
    scheduled_posts = list(post_qs[:80])

    beat_rows, beat_err = beat_periodic_preview(50)
    tr_sub = (request.GET.get('tr_q') or '').strip()
    if staff:
        task_results, tr_err = recent_task_results(
            limit=60, task_name_contains=tr_sub, status=tr_status
        )
    else:
        task_results, tr_err = recent_task_results(
            limit=40,
            task_name_contains=tr_sub,
            status=tr_status,
            categories_only=['parse', 'import'],
        )

    ctx = {
        'filter_cat': cat,
        'filter_task_q': name_sub,
        'filter_hist_status': hist_st,
        'filter_parse': parse_f,
        'filter_post_status': post_st,
        'filter_tr_status': tr_status,
        'filter_tr_q': tr_sub,
        'queues': queues,
        'queues_error': q_err,
        'inspect_error': insp.get('error'),
        'workers_ping': insp.get('ping') or {},
        'active_tasks': active_f,
        'reserved_tasks': reserved_f,
        'scheduled_worker_tasks': insp.get('scheduled') or {},
        'worker_stats': insp.get('stats') or {},
        'settings_celery': settings_celery_summary(),
        'beat_tasks': beat_rows,
        'beat_error': beat_err,
        'history_runs': history_runs,
        'parse_tasks': parse_tasks,
        'scheduled_posts': scheduled_posts,
        'task_results': task_results,
        'task_results_error': tr_err,
        'support_commands': support_log_commands(),
        'support_hint': parse_support_bundle_from_logs_hint(),
        'show_full_internals': staff,
        'task_category': task_category,
    }
    return render(request, 'core/celery_monitor.html', ctx)


@login_required
def celery_monitor_snapshot(request):
    """Лёгкий JSON для автообновления блока «Сейчас воркер»."""
    if not _user_can_celery_monitor(request.user):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    from .celery_monitor_service import (
        celery_inspect_bundle,
        filter_tasks_by_category,
        filter_tasks_by_name,
        redis_queue_lengths,
        task_category,
    )

    cat = (request.GET.get('cat') or 'all').strip().lower()
    if cat not in ('all', 'publish', 'parse', 'import', 'other'):
        cat = 'all'
    name_sub = (request.GET.get('task_q') or '').strip()

    broker = (getattr(settings, 'CELERY_BROKER_URL', None) or '').strip()
    queues, q_err = redis_queue_lengths(broker)
    insp = celery_inspect_bundle(timeout=1.0, include_scheduled_stats=False)
    active = filter_tasks_by_name(
        filter_tasks_by_category(dict(insp.get('active') or {}), cat),
        name_sub,
    )
    reserved = filter_tasks_by_name(
        filter_tasks_by_category(dict(insp.get('reserved') or {}), cat),
        name_sub,
    )

    def _strip_task(t: dict) -> dict:
        return {
            'id': t.get('id'),
            'name': t.get('name'),
            'category': task_category(str(t.get('name') or '')),
        }

    out_active = {
        w: [_strip_task(t) for t in (lst or [])]
        for w, lst in active.items()
    }
    out_reserved = {
        w: [_strip_task(t) for t in (lst or [])]
        for w, lst in reserved.items()
    }

    payload = {
        'ok': True,
        'ts': timezone.now().isoformat(timespec='seconds'),
        'queues': queues,
        'queues_error': q_err,
        'inspect_error': insp.get('error'),
        'workers': list((insp.get('ping') or {}).keys()),
        'active': out_active,
        'reserved': out_reserved,
    }
    return JsonResponse(payload)
