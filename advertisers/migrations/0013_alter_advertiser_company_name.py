# Синхронизация с models.Advertiser.company_name (verbose_name + help_text).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0012_payment_link_and_legacy_tbank'),
    ]

    operations = [
        migrations.AlterField(
            model_name='advertiser',
            name='company_name',
            field=models.CharField(
                help_text='Юрлицо с ОПФ, ИП — ФИО, физлицо — ФИО полностью.',
                max_length=255,
                verbose_name='Название компании, ИП или физлица',
            ),
        ),
    ]
