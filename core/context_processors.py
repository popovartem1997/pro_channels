"""
Глобальные контекстные переменные для шаблонов.
"""
from django.conf import settings
from django.db import models

from parsing.deepseek_snippet import AI_POST_MOODS


def site_context(request):
    ctx = {
        'SITE_NAME': settings.SITE_NAME,
        'SITE_URL': settings.SITE_URL,
        'AI_REWRITE_ENABLED': getattr(settings, 'AI_REWRITE_ENABLED', False),
        'ai_post_moods': AI_POST_MOODS,
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
                    models.Q(bot__channel_groups__channels__pk__in=allowed_channel_ids)
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
        # Badge возле "Лента": pending предложка + новые материалы парсинга.
        parsed_new = 0
        try:
            from parsing.models import ParsedItem
            if role in ('manager', 'assistant_admin') and not (request.user.is_staff or request.user.is_superuser):
                from managers.models import TeamMember
                ch_ids = TeamMember.objects.filter(member=request.user, is_active=True).filter(
                    models.Q(can_publish=True) | models.Q(can_moderate=True)
                ).values_list('channels__pk', flat=True)
                parsed_new = ParsedItem.objects.filter(
                    status=ParsedItem.STATUS_NEW,
                    keyword__channel_id__in=ch_ids,
                ).distinct().count()
            else:
                if request.user.is_staff or request.user.is_superuser:
                    parsed_new = ParsedItem.objects.filter(status=ParsedItem.STATUS_NEW).count()
                else:
                    parsed_new = ParsedItem.objects.filter(
                        status=ParsedItem.STATUS_NEW
                    ).filter(
                        models.Q(source__owner=request.user) | models.Q(keyword__owner=request.user)
                    ).distinct().count()
        except Exception:
            parsed_new = 0

        ctx['global_pending_count'] = int(pending_count or 0) + int(parsed_new or 0)
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
