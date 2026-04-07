"""
Статистика по каналам и постам.
"""
import json
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Q, Sum, Count, Case, When, Value, F, FloatField
from django.db.models.functions import Cast
from django.urls import reverse
from datetime import timedelta


def _chgroup_param_for_parse_keyword(kw):
    ch = kw.channel
    if ch is not None and getattr(ch, 'channel_group_id', None):
        return str(ch.channel_group_id)
    if kw.channel_group_id:
        return str(kw.channel_group_id)
    return 'all'


@login_required
def stats_dashboard(request):
    from channels.models import Channel
    from parsing.models import ParseKeyword
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

    kw_sort = (request.GET.get('kw_sort') or 'keyword').strip()
    kw_dir = (request.GET.get('kw_dir') or 'asc').strip()
    if kw_sort not in ('keyword', 'published', 'skipped', 'created', 'rate'):
        kw_sort = 'keyword'
    if kw_dir not in ('asc', 'desc'):
        kw_dir = 'asc'

    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin'):
        from managers.models import TeamMember

        parse_ch_ids = list(
            TeamMember.objects.filter(member=request.user, is_active=True)
            .filter(Q(can_publish=True) | Q(can_moderate=True))
            .values_list('channels__pk', flat=True)
            .distinct()
        )
        kw_qs = ParseKeyword.objects.filter(channel_id__in=parse_ch_ids)
    else:
        kw_qs = ParseKeyword.objects.filter(owner=request.user)

    kw_qs = kw_qs.select_related('channel', 'channel_group')
    kw_qs = kw_qs.annotate(_dec=F('stats_skipped') + F('stats_published')).annotate(
        _rate=Case(
            When(_dec=0, then=Value(0.0)),
            default=Cast(F('stats_published'), FloatField()) / Cast(F('_dec'), FloatField()),
            output_field=FloatField(),
        ),
    )
    order_field = {
        'keyword': 'keyword',
        'published': 'stats_published',
        'skipped': 'stats_skipped',
        'created': 'created_at',
        'rate': '_rate',
    }.get(kw_sort, 'keyword')
    ord_prefix = '' if kw_dir == 'asc' else '-'
    if kw_sort == 'keyword':
        kw_qs = kw_qs.order_by(f'{ord_prefix}keyword', 'pk')
    else:
        kw_qs = kw_qs.order_by(f'{ord_prefix}{order_field}', 'keyword')

    keyword_stat_rows = []
    for kw in kw_qs:
        dec = int(kw._dec)
        keyword_stat_rows.append({
            'kw': kw,
            'chgroup_param': _chgroup_param_for_parse_keyword(kw),
            'conversion_pct': round(100.0 * kw.stats_published / dec, 1) if dec else None,
        })

    stats_dashboard_url = reverse('stats:dashboard')

    return render(request, 'stats/dashboard.html', {
        'channel_data': channel_data,
        'date_range': f'{week_ago:%d.%m} — {today:%d.%m.%Y}',
        'keyword_stat_rows': keyword_stat_rows,
        'kw_sort': kw_sort,
        'kw_dir': kw_dir,
        'stats_dashboard_url': stats_dashboard_url,
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
    n_posts = int(agg['n'] or 0)
    if tot_v > 0:
        engagement_rate_posts = round(engagement_actions / tot_v * 100, 2)
    elif n_posts > 0:
        # Строки PostStat есть, просмотры пока 0 — не None, иначе в шаблоне «—»
        engagement_rate_posts = 0.0
    else:
        engagement_rate_posts = None

    # ER канала: взвешенное среднее по снимкам ChannelStat (дневной ER × дневные просмотры в снимке).
    w_er_num = sum((s.er or 0) * (s.views or 0) for s in stats)
    w_er_den = sum(s.views or 0 for s in stats)
    avg_er = round(w_er_num / w_er_den, 2) if w_er_den else None

    # Если в снимках нет дневных просмотров — тот же смысл, что «ER постов»: действия / просмотры постов.
    if avg_er is None and tot_v > 0:
        avg_er = engagement_rate_posts

    # Оценка по охвату и базе подписчиков, если постовой агрегат пустой
    if avg_er is None:
        subs_now = int(getattr(channel, 'subscribers_count', 0) or 0)
        total_views_period_chk = tot_v if tot_v else sum(views_data)
        if subs_now > 0 and total_views_period_chk > 0 and n_posts > 0:
            avg_reach = total_views_period_chk / float(n_posts)
            avg_er = round(avg_reach / subs_now * 100, 2)

    if avg_er is None:
        avg_er = 0.0

    # Fallbacks when daily snapshots (ChannelStat) are empty or have zero views.
    total_views_period = int(tot_v if tot_v else sum(views_data))

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
        'avg_er': avg_er,
        'stat_agg_views': tot_v,
        'stat_agg_reactions': tot_r,
        'stat_agg_comments': tot_c,
        'stat_agg_forwards': tot_f,
        'stat_agg_post_rows': n_posts,
        'engagement_rate_posts': engagement_rate_posts,
        'subs_delta': subs_delta,
    })
