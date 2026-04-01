from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0007_channelgroup_channel_channel_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='channel',
            name='ord_pad_external_id',
            field=models.CharField(
                blank=True,
                help_text='Площадка в кабинете ОРД для передачи статистики по этому каналу.',
                max_length=220,
                verbose_name='ОРД VK: внешний ID площадки',
            ),
        ),
    ]
