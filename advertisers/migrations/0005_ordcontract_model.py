from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0004_advertiser_ord_person_external_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrdContract',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('external_id', models.CharField(db_index=True, max_length=220, unique=True, verbose_name='Внешний ID договора (ОРД)')),
                ('type', models.CharField(blank=True, max_length=60, verbose_name='Тип договора (ОРД)')),
                ('client_external_id', models.CharField(blank=True, max_length=220, verbose_name='Клиент (person external_id)')),
                ('contractor_external_id', models.CharField(blank=True, max_length=220, verbose_name='Исполнитель (person external_id)')),
                ('date', models.CharField(blank=True, max_length=20, verbose_name='Дата договора')),
                ('serial', models.CharField(blank=True, max_length=120, verbose_name='Номер/серия')),
                ('raw', models.JSONField(default=dict, verbose_name='RAW из ОРД')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлён')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
                ('advertiser', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ord_contracts', to='advertisers.advertiser', verbose_name='Рекламодатель (сопоставленный)')),
            ],
            options={
                'verbose_name': 'ОРД договор',
                'verbose_name_plural': 'ОРД договоры',
                'ordering': ['-updated_at'],
            },
        ),
    ]

