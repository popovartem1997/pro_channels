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
        # Pending suggestions badge:
        # - owner/staff: their bots (or all for staff)
        # - manager/assistant: only suggestions in channels they can moderate + bots where they are moderator
        role = getattr(request.user, 'role', '') or ''
        if role in ('manager', 'assistant_admin') and not (request.user.is_staff or request.user.is_superuser):
            try:
                from managers.models import TeamMember
                allowed_channel_ids = TeamMember.objects.filter(
                    member=request.user,
                    is_active=True,
                    can_moderate=True,
                ).values_list('channels__pk', flat=True)
                pending_count = Suggestion.objects.filter(
                    status='pending'
                ).filter(
                    models.Q(bot__channel_id__in=allowed_channel_ids)
                    | models.Q(bot__moderators=request.user)
                ).distinct().count()
            except Exception:
                pending_count = Suggestion.objects.filter(
                    models.Q(bot__moderators=request.user),
                    status='pending'
                ).distinct().count()
        else:
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
