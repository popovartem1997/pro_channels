from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages


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
    audit = AuditLog.objects.select_related('actor', 'owner').all().order_by('-created_at')[:300]
    visits = PageVisit.objects.select_related('user').all().order_by('-created_at')[:300]
    return render(request, 'core/audit_log.html', {'audit': audit, 'visits': visits})
