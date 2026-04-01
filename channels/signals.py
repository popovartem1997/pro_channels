from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Channel


@receiver(pre_save, sender=Channel)
def channel_pre_save_group(sender, instance, **kwargs):
    if not instance.pk:
        instance._old_channel_group_id = None
        return
    try:
        prev = Channel.objects.get(pk=instance.pk)
        instance._old_channel_group_id = prev.channel_group_id
    except Channel.DoesNotExist:
        instance._old_channel_group_id = None


@receiver(post_save, sender=Channel)
def channel_post_save_parse_tasks(sender, instance, **kwargs):
    try:
        from parsing.schedule_sync import (
            sync_auto_parse_tasks_after_group_change,
            sync_auto_parse_tasks_for_channel,
        )

        sync_auto_parse_tasks_for_channel(instance)
        old_gid = getattr(instance, '_old_channel_group_id', None)
        if old_gid and old_gid != instance.channel_group_id:
            sync_auto_parse_tasks_after_group_change(instance.owner_id, old_gid)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning('parse task sync skipped: %s', e)
