"""
Статистика по каналам и постам.
"""
import json
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Sum, Count
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
    posts_per_day = [s.posts_count for s in stats]

    since_dt = timezone.now() - timedelta(days=period)
    agg = PostStat.objects.filter(
        channel=channel,
        post__published_at__isnull=False,
        post__published_at__gte=since_dt,
    ).aggregate(
        v=Sum('views'),
        r=Sum('reactions'),
        c=Sum('comments'),
        f=Sum('forwards'),
        n=Count('id'),
    )
    tot_v = int(agg['v'] or 0)
    tot_r = int(agg['r'] or 0)
    tot_c = int(agg['c'] or 0)
    tot_f = int(agg['f'] or 0)
    engagement_actions = tot_r + tot_c + tot_f
    engagement_rate_posts = round(engagement_actions / tot_v * 100, 2) if tot_v else None

    w_er_num = sum((s.er or 0) * (s.views or 0) for s in stats)
    w_er_den = sum(s.views or 0 for s in stats)
    avg_er_weighted = round(w_er_num / w_er_den, 2) if w_er_den else None

    # Fallbacks when daily snapshots (ChannelStat) are empty or have zero views.
    # - total_views: prefer post views sum for the same period
    # - ER channel: use avg reach per post / subscribers * 100 (rough estimate)
    total_views_period = tot_v if tot_v else sum(views_data)
    if avg_er_weighted is None:
        subs_now = int(getattr(channel, 'subscribers_count', 0) or 0)
        n_posts = int(agg['n'] or 0)
        if subs_now > 0 and tot_v > 0 and n_posts > 0:
            avg_reach = tot_v / n_posts
            avg_er_weighted = round(avg_reach / subs_now * 100, 2)
        else:
            avg_er_weighted = 0.0

    subs_delta = None
    if len(subs_data) >= 2:
        subs_delta = subs_data[-1] - subs_data[0]

    return render(request, 'stats/channel.html', {
        'channel': channel,
        'stats': stats,
        'post_stats': post_stats,
        'period': period,
        'labels': json.dumps(labels, ensure_ascii=False),
        'views_data': json.dumps(views_data, ensure_ascii=False),
        'subs_data': json.dumps(subs_data, ensure_ascii=False),
        'posts_per_day': json.dumps(posts_per_day, ensure_ascii=False),
        'total_views': total_views_period,
        'avg_er': avg_er_weighted,
        'stat_agg_views': tot_v,
        'stat_agg_reactions': tot_r,
        'stat_agg_comments': tot_c,
        'stat_agg_forwards': tot_f,
        'stat_agg_post_rows': int(agg['n'] or 0),
        'engagement_rate_posts': engagement_rate_posts,
        'subs_delta': subs_delta,
    })
