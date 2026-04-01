from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ord_marking', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordregistration',
            name='creative_external_id',
            field=models.CharField(blank=True, db_index=True, max_length=220, verbose_name='Внешний ID креатива в ОРД'),
        ),
        migrations.AddField(
            model_name='ordregistration',
            name='erid',
            field=models.CharField(blank=True, max_length=120, verbose_name='ERID (маркер)'),
        ),
        migrations.AddField(
            model_name='ordregistration',
            name='contract_external_id',
            field=models.CharField(blank=True, max_length=220, verbose_name='Договор ОРД (override)'),
        ),
        migrations.AddField(
            model_name='ordregistration',
            name='pad_external_id',
            field=models.CharField(blank=True, max_length=220, verbose_name='Площадка ОРД (override)'),
        ),
        migrations.AddField(
            model_name='ordregistration',
            name='person_external_id',
            field=models.CharField(blank=True, max_length=220, verbose_name='Контрагент ОРД (override)'),
        ),
        migrations.AddField(
            model_name='ordregistration',
            name='stats_submitted_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Статистика отправлена'),
        ),
        migrations.AddField(
            model_name='ordregistration',
            name='stats_error_message',
            field=models.TextField(blank=True, verbose_name='Ошибка статистики'),
        ),
        migrations.AddField(
            model_name='ordregistration',
            name='stats_raw_response',
            field=models.JSONField(default=dict, verbose_name='Ответ по статистике'),
        ),
        migrations.AlterField(
            model_name='ordregistration',
            name='ord_id',
            field=models.CharField(blank=True, max_length=200, verbose_name='Служебный ID'),
        ),
        migrations.AlterField(
            model_name='ordregistration',
            name='ord_token',
            field=models.CharField(blank=True, max_length=500, verbose_name='Токен маркировки (как erid)'),
        ),
    ]
