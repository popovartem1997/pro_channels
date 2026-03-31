"""
Глобальные контекстные переменные для шаблонов.
"""
from django.conf import settings
from django.db import models


def site_context(request):
    ctx = {
        'SITE_NAME': settings.SITE_NAME,
        'SITE_URL': settings.SITE_URL,
        'AI_REWRITE_ENABLED': getattr(settings, 'AI_REWRITE_ENABLED', False),
    }
    if request.user.is_authenticated:
        from bots.models import Suggestion
        # Owner sees all his bots; moderator sees only assigned bots
        pending_count = Suggestion.objects.filter(
            models.Q(bot__owner=request.user) | models.Q(bot__moderators=request.user),
            status='pending'
        ).distinct().count()
        ctx['global_pending_count'] = pending_count
        if request.user.is_staff or getattr(request.user, 'role', '') == 'owner':
            from advertisers.models import AdvertisingOrder
            ctx['adv_pending_count'] = AdvertisingOrder.objects.filter(
                status='submitted'
            ).count()
    return ctx
