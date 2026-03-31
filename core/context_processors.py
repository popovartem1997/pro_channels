"""
Глобальные контекстные переменные для шаблонов.
"""
from django.conf import settings


def site_context(request):
    ctx = {
        'SITE_NAME': settings.SITE_NAME,
        'SITE_URL': settings.SITE_URL,
        'AI_REWRITE_ENABLED': getattr(settings, 'AI_REWRITE_ENABLED', False),
    }
    if request.user.is_authenticated:
        from bots.models import Suggestion
        pending_count = Suggestion.objects.filter(
            bot__owner=request.user, status='pending'
        ).count()
        ctx['global_pending_count'] = pending_count
        if request.user.is_staff or getattr(request.user, 'role', '') == 'owner':
            from advertisers.models import AdvertisingOrder
            ctx['adv_pending_count'] = AdvertisingOrder.objects.filter(
                status='submitted'
            ).count()
    return ctx
