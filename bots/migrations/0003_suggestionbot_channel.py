from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0003_per_platform_footers'),
        ('bots', '0002_moderation_recipients'),
    ]

    operations = [
        migrations.AddField(
            model_name='suggestionbot',
            name='channel',
            field=models.ForeignKey(
                blank=True,
                help_text='К какому каналу относится этот бот (для прав менеджеров и логики предложки).',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='suggestion_bots',
                to='channels.channel',
                verbose_name='Канал',
            ),
        ),
    ]

