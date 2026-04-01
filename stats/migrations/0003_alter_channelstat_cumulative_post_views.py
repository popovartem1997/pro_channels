# Generated manually — синхронизация help_text с models.ChannelStat

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stats', '0002_channelstat_cumulative_post_views'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channelstat',
            name='cumulative_post_views',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Для расчёта дневного прироста просмотров',
                verbose_name='Сумма просмотров постов (снимок)',
            ),
        ),
    ]
