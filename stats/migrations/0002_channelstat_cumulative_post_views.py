# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stats', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='channelstat',
            name='cumulative_post_views',
            field=models.PositiveIntegerField(
                default=0,
                verbose_name='Сумма просмотров постов (снимок)',
                help_text='Для расчёта прироста просмотров за день относительно предыдущего дня',
            ),
        ),
    ]
