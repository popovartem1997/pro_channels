"""
Middleware для проверки подписки.
Перенаправляет на страницу оплаты если подписка истекла.
"""
import time
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

# Пути, доступные без подписки (после окончания trial)
EXEMPT_URLS = [
    '/login/', '/logout/', '/register/', '/verify-email/', '/reset-password/',
    '/billing/', '/admin/', '/static/', '/media/',
    '/offer/', '/privacy/', '/',
    '/channels/',  # пользователь должен иметь возможность добавить канал, чтобы оплатить его
]


class SubscriptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.is_staff:
            path = request.path
            # Не проверяем exempt urls
            is_exempt = any(path.startswith(url) for url in EXEMPT_URLS)
            if not is_exempt:
                user = request.user
                # Рекламодатели не должны проходить проверку подписки на каналы.
                if getattr(user, 'role', None) == getattr(user, 'ROLE_ADVERTISER', 'advertiser'):
                    return self.get_response(request)
                # Если trial ещё активен — пропускаем
                if user.is_on_trial:
                    pass
                else:
                    # Проверяем наличие активной подписки
                    from billing.models import SubscriptionPurchase
                    has_active = SubscriptionPurchase.objects.filter(
                        user=user,
                        is_active=True,
                        ends_at__gt=timezone.now()
                    ).exists()
                    if not has_active:
                        return redirect('/billing/subscribe/')
        return self.get_response(request)


class PageVisitMiddleware:
    """Пишет посещения страниц в базу (только для авторизованных)."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        t0 = time.monotonic()
        response = self.get_response(request)
        try:
            if getattr(request, "user", None) and request.user.is_authenticated:
                # Не логируем статику/медиа и вебхуки, чтобы не засорять базу.
                path = request.path or ""
                if path.startswith("/static/") or path.startswith("/media/") or path.startswith("/bots/webhook/"):
                    return response
                from core.models import PageVisit
                ip = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")
                PageVisit.objects.create(
                    user=request.user,
                    method=(request.method or "")[:10],
                    path=path[:500],
                    query_string=(request.META.get("QUERY_STRING") or "")[:5000],
                    referer=(request.META.get("HTTP_REFERER") or "")[:500],
                    user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:5000],
                    ip=(ip or "")[:64],
                    status_code=getattr(response, "status_code", None),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
        except Exception:
            # Логи не должны ломать работу сайта
            pass
        return response
