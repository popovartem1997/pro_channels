from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0003_advertisingorder_repeat_interval_days'),
    ]

    operations = [
        migrations.AddField(
            model_name='advertiser',
            name='ord_person_external_id',
            field=models.CharField(
                blank=True,
                help_text='Из кабинета ord.vk.com — для креативов с привязкой к рекламодателю (person).',
                max_length=220,
                verbose_name='Внешний ID в ОРД VK (контрагент)',
            ),
        ),
    ]
