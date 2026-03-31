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
        # Меню: показывать "Статистика" менеджеру только если ему выдали это право.
        if getattr(request.user, 'role', '') in ('manager', 'assistant_admin'):
            try:
                from managers.models import TeamMember
                ctx['manager_can_view_stats'] = TeamMember.objects.filter(
                    member=request.user,
                    is_active=True,
                    can_view_stats=True,
                ).exists()
                ctx['manager_can_manage_bots'] = TeamMember.objects.filter(
                    member=request.user,
                    is_active=True,
                    can_manage_bots=True,
                ).exists()
            except Exception:
                ctx['manager_can_view_stats'] = False
                ctx['manager_can_manage_bots'] = False
    return ctx
