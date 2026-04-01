"""
Синхронизация авто-задач парсинга (ParseTask) с группами каналов и отдельными каналами.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _ensure_scheduler():
    try:
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        every_20m, _ = IntervalSchedule.objects.get_or_create(every=20, period=IntervalSchedule.MINUTES)
        PeriodicTask.objects.update_or_create(
            name='parsing: check parse tasks (every 20m)',
            defaults={
                'interval': every_20m,
                'task': 'parsing.tasks.check_parse_tasks',
                'enabled': True,
            },
        )
    except Exception:
        pass


def sync_auto_parse_tasks_for_channel(channel) -> None:
    """
    Обновить авто-задачу: для канала в группе — одна задача на всю группу;
    без группы — отдельная задача на канал.
    """
    from channels.models import Channel
    from parsing.models import ParseKeyword, ParseSource, ParseTask

    owner = channel.owner
    if not owner:
        return

    _ensure_scheduler()

    if channel.channel_group_id:
        g = channel.channel_group
        chs = Channel.objects.filter(owner=owner, channel_group=g, is_active=True)
        sources = list(
            ParseSource.objects.filter(owner=owner, channel__in=chs, is_active=True)
            .values_list('pk', flat=True)
            .distinct()
        )
        keywords = list(
            ParseKeyword.objects.filter(owner=owner, channel__in=chs, is_active=True)
            .values_list('pk', flat=True)
            .distinct()
        )
        task_name = f'Auto parsing (группа «{g.name}»)'
        task, _ = ParseTask.objects.update_or_create(
            owner=owner,
            name=task_name,
            defaults={'schedule_cron': '*/20 * * * *', 'is_active': True},
        )
        if task.schedule_cron != '*/20 * * * *':
            task.schedule_cron = '*/20 * * * *'
            task.is_active = True
            task.save(update_fields=['schedule_cron', 'is_active'])
        task.sources.set(sources)
        task.keywords.set(keywords)
        # Иначе остаются старые «Auto parsing (channel N)» — дубли и пустые прогоны.
        for c in chs:
            ParseTask.objects.filter(owner=owner, name=f'Auto parsing (channel {c.pk})').delete()
        return

    # Канал без группы — как раньше, отдельная задача
    sources = list(
        ParseSource.objects.filter(owner=owner, channel=channel, is_active=True).values_list('pk', flat=True)
    )
    keywords = list(
        ParseKeyword.objects.filter(owner=owner, channel=channel, is_active=True).values_list('pk', flat=True)
    )
    task, _ = ParseTask.objects.update_or_create(
        owner=owner,
        name=f'Auto parsing (channel {channel.pk})',
        defaults={'schedule_cron': '*/20 * * * *', 'is_active': True},
    )
    if task.schedule_cron != '*/20 * * * *':
        task.schedule_cron = '*/20 * * * *'
        task.is_active = True
        task.save(update_fields=['schedule_cron', 'is_active'])
    task.sources.set(sources)
    task.keywords.set(keywords)


def sync_auto_parse_tasks_after_group_change(owner_id: int, old_group_id: int | None) -> None:
    """После смены группы у канала пересчитать задачу старой группы (или удалить, если каналов не осталось)."""
    if not old_group_id:
        return
    from channels.models import Channel, ChannelGroup
    from parsing.models import ParseKeyword, ParseSource, ParseTask

    try:
        g = ChannelGroup.objects.get(pk=old_group_id)
    except ChannelGroup.DoesNotExist:
        return

    chs = Channel.objects.filter(owner_id=owner_id, channel_group=g, is_active=True)
    if not chs.exists():
        ParseTask.objects.filter(owner_id=owner_id, name=f'Auto parsing (группа «{g.name}»)').delete()
        return

    first = chs.first()
    sync_auto_parse_tasks_for_channel(first)
