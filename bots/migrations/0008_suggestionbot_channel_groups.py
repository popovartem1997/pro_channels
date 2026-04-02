# Generated manually for SuggestionBot → ChannelGroup M2M

from django.db import migrations, models


def forwards_copy_channel_to_groups(apps, schema_editor):
    SuggestionBot = apps.get_model('bots', 'SuggestionBot')
    Channel = apps.get_model('channels', 'Channel')
    for bot in SuggestionBot.objects.all().iterator():
        cid = getattr(bot, 'channel_id', None)
        if not cid:
            continue
        try:
            ch = Channel.objects.get(pk=cid)
        except Channel.DoesNotExist:
            continue
        gid = getattr(ch, 'channel_group_id', None)
        if gid:
            bot.channel_groups.add(gid)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('bots', '0007_maxprocessedcallback'),
        ('channels', '0009_historyimportrun'),
    ]

    operations = [
        migrations.AddField(
            model_name='suggestionbot',
            name='channel_groups',
            field=models.ManyToManyField(
                blank=True,
                related_name='suggestion_bots',
                to='channels.channelgroup',
                verbose_name='Группы каналов',
                help_text='Черновик поста из предложки получит все активные каналы из выбранных групп.',
            ),
        ),
        migrations.RunPython(forwards_copy_channel_to_groups, noop_reverse),
        migrations.RemoveField(
            model_name='suggestionbot',
            name='channel',
        ),
    ]
