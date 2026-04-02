from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ord_marking', '0003_ordsyncrun_model'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrdContractExternalIdCounter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveIntegerField(db_index=True, unique=True, verbose_name='Год')),
                ('last_seq', models.PositiveIntegerField(default=0, verbose_name='Последний номер')),
            ],
            options={
                'verbose_name': 'Счётчик id договора ОРД',
                'verbose_name_plural': 'Счётчики id договоров ОРД',
            },
        ),
    ]
