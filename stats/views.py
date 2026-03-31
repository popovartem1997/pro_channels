"""
Статистика по каналам и постам.
"""
import json
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import timedelta


@login_required
def stats_dashboard(request):
    from channels.models import Channel
    from .models import ChannelStat, PostStat
    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        channels = Channel.objects.filter(
            pk__in=TeamMember.objects.filter(
                member=request.user,
                is_active=True,
                can_view_stats=True,
            ).values_list('channels__pk', flat=True)
        ).distinct()
    else:
        channels = Channel.objects.filter(owner=request.user)
    today = timezone.now().date()
    week_ago = today - timedelta(days=7)

    # Суммарные данные по каналам за последние 7 дней
    channel_data = []
    for ch in channels:
        stats = ChannelStat.objects.filter(channel=ch, date__gte=week_ago).order_by('date')
        stats_list = list(stats)
        stats_json = [
            {'date': s.date.strftime('%d.%m'), 'views': s.views, 'subscribers': s.subscribers}
            for s in stats_list
        ]
        channel_data.append({
            'channel': ch,
            'stats': stats_json,
            'stats_json': json.dumps(stats_json, ensure_ascii=False),
            'total_views': sum(s.views for s in stats_list),
            'latest_subscribers': stats_list[-1].subscribers if stats_list else 0,
        })

    return render(request, 'stats/dashboard.html', {
        'channel_data': channel_data,
        'date_range': f'{week_ago:%d.%m} — {today:%d.%m.%Y}',
    })


@login_required
def channel_stats(request, channel_pk):
    from channels.models import Channel
    from .models import ChannelStat, PostStat
    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        allowed_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_view_stats=True,
        ).values_list('channels__pk', flat=True)
        channel = get_object_or_404(Channel.objects.filter(pk__in=allowed_ids).distinct(), pk=channel_pk)
    else:
        channel = get_object_or_404(Channel, pk=channel_pk, owner=request.user)

    period = int(request.GET.get('period', 30))
    since = timezone.now().date() - timedelta(days=period)
    stats = ChannelStat.objects.filter(channel=channel, date__gte=since).order_by('date')

    post_stats = PostStat.objects.filter(
        channel=channel
    ).select_related('post').order_by('-post__published_at')[:20]

    # Для графика
    labels = [s.date.strftime('%d.%m') for s in stats]
    views_data = [s.views for s in stats]
    subs_data = [s.subscribers for s in stats]

    return render(request, 'stats/channel.html', {
        'channel': channel,
        'stats': stats,
        'post_stats': post_stats,
        'period': period,
        'labels': labels,
        'views_data': views_data,
        'subs_data': subs_data,
        'total_views': sum(views_data),
        'avg_er': round(sum(s.er for s in stats) / len(stats), 2) if stats else 0,
    })
