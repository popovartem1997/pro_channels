# Sync M2M help_text with models (0008 used older wording).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bots', '0008_suggestionbot_channel_groups'),
        ('channels', '0009_historyimportrun'),
    ]

    operations = [
        migrations.AlterField(
            model_name='suggestionbot',
            name='channel_groups',
            field=models.ManyToManyField(
                blank=True,
                help_text='Предложки и черновики постов привязываются ко всем активным каналам выбранных групп.',
                related_name='suggestion_bots',
                to='channels.channelgroup',
                verbose_name='Группы каналов',
            ),
        ),
    ]
