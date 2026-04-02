# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0008_adapplication_owner_review'),
    ]

    operations = [
        migrations.AddField(
            model_name='adapplication',
            name='ad_pin_hours',
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text='Если у канала включён закреп с ценой за час — сколько часов оплатил рекламодатель.',
                verbose_name='Закреп: выбрано часов (почасовая опция)',
            ),
        ),
    ]
