from django.shortcuts import render, redirect
from django.http import HttpResponse


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
