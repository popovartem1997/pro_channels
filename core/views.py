from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone


def home(request):
    # Если пользователь уже вошёл — показываем кабинет, а не лендинг
    if getattr(request, "user", None) and request.user.is_authenticated:
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
        audit_qs = audit_qs.filter(action__icontains=q_action)
    if q_actor:
        audit_qs = audit_qs.filter(actor__username__icontains=q_actor)
    if q_object_type:
        audit_qs = audit_qs.filter(object_type__icontains=q_object_type)

    if q_path:
        visits_qs = visits_qs.filter(path__icontains=q_path)
    if q_ip:
        visits_qs = visits_qs.filter(ip__icontains=q_ip)

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

    audit = audit_qs.order_by('-created_at')[:500]
    visits = visits_qs.order_by('-created_at')[:500]

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

    visits_enriched = []
    for v in visits:
        visits_enriched.append({
            'obj': v,
            'view_name': _resolve_name(v.path),
        })

    return render(request, 'core/audit_log.html', {
        'audit': audit,
        'visits_enriched': visits_enriched,
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


@login_required
def feed(request):
    """
    Единая лента: посты + предложка (от подписчиков) + парсинг.
    Адаптирована под моб/десктоп, с фильтрами.
    """
    from content.models import Post
    from bots.models import Suggestion
    from parsing.models import ParsedItem

    kind = (request.GET.get('kind') or 'all').strip()  # all|post|subscriber|parsing
    status_filter = (request.GET.get('status') or '').strip()  # for subscriber/parsing

    # Visibility scopes
    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin') and not (request.user.is_staff or request.user.is_superuser):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
        ).values_list('channels__pk', flat=True)
        allowed_channel_ids = list(set(int(x) for x in allowed_channel_ids if str(x).isdigit()))
        post_qs = Post.objects.filter(channels__pk__in=allowed_channel_ids).distinct()
        sug_qs = Suggestion.objects.filter(bot__channel_id__in=allowed_channel_ids).distinct()
        parsed_qs = ParsedItem.objects.filter(source__channel_id__in=allowed_channel_ids)
    else:
        # owner/staff
        post_qs = Post.objects.filter(author=request.user) if not (request.user.is_staff or request.user.is_superuser) else Post.objects.all()
        sug_qs = Suggestion.objects.filter(bot__owner=request.user) if not (request.user.is_staff or request.user.is_superuser) else Suggestion.objects.all()
        parsed_qs = ParsedItem.objects.filter(source__owner=request.user) if not (request.user.is_staff or request.user.is_superuser) else ParsedItem.objects.all()

    post_qs = post_qs.prefetch_related('channels').select_related('author', 'published_by')
    sug_qs = sug_qs.select_related('bot', 'bot__channel')
    parsed_qs = parsed_qs.select_related('source', 'source__channel', 'keyword')

    items = []

    if kind in ('all', 'post'):
        for p in post_qs.order_by('-created_at')[:200]:
            items.append({
                'kind': 'post',
                'dt': p.created_at,
                'title': 'Пост',
                'text': p.text or '',
                'status': p.status,
                'channels': list(p.channels.all()),
                'url': f'/posts/{p.pk}/',
                'meta': f'Автор: {getattr(p.author, "username", "—")}' + (f' · Опубликовал: {p.published_by.username}' if getattr(p, 'published_by', None) else ''),
            })

    if kind in ('all', 'subscriber'):
        q = sug_qs.order_by('-submitted_at')
        if status_filter:
            q = q.filter(status=status_filter)
        for s in q[:200]:
            items.append({
                'kind': 'subscriber',
                'dt': s.submitted_at,
                'title': 'От подписчика',
                'text': s.text or '',
                'status': s.status,
                'channels': [s.bot.channel] if getattr(s.bot, 'channel', None) else [],
                'url': '',
                'meta': f'{s.bot.name} · {s.sender_display}',
            })

    if kind in ('all', 'parsing'):
        q = parsed_qs.order_by('-found_at')
        # map ParsedItem status to suggestion-like statuses for filtering
        if status_filter:
            if status_filter == 'pending':
                q = q.filter(status=ParsedItem.STATUS_NEW)
            elif status_filter == 'rejected':
                q = q.filter(status=ParsedItem.STATUS_IGNORED)
            elif status_filter == 'published':
                q = q.filter(status=ParsedItem.STATUS_USED)
        for pi in q[:200]:
            items.append({
                'kind': 'parsing',
                'dt': pi.found_at,
                'title': 'Парсинг',
                'text': pi.text or '',
                'status': 'pending' if pi.status == ParsedItem.STATUS_NEW else ('published' if pi.status == ParsedItem.STATUS_USED else 'rejected'),
                'channels': [pi.source.channel] if getattr(pi.source, 'channel', None) else [],
                'url': pi.original_url or '',
                'meta': f'{pi.source.get_platform_display()} · {pi.source.name}',
            })

    items.sort(key=lambda x: x.get('dt') or timezone.now(), reverse=True)
    items = items[:300]

    return render(request, 'core/feed.html', {
        'items': items,
        'kind': kind,
        'status_filter': status_filter,
    })
