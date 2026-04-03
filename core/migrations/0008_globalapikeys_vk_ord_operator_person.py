from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_celery_taskresult_date_done_id_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='globalapikeys',
            name='vk_ord_operator_person_external_id',
            field=models.CharField(
                blank=True,
                help_text='Внешний id контрагента в ОРД для вашей организации (исполнитель по договору с рекламодателем). '
                'Нужен для автоматического создания договора; person рекламодателя создаётся без этого поля.',
                max_length=220,
                verbose_name='ОРД: person исполнителя (оператор ProChannels)',
            ),
        ),
    ]
