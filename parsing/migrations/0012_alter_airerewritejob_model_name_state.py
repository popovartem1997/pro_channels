"""
Синхронизация state миграций с default='deepseek-chat' (БД уже обновлена в 0011).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parsing', '0011_airerewritejob_model_default_deepseek'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name='airewritejob',
                    name='model_name',
                    field=models.CharField(
                        default='deepseek-chat',
                        max_length=100,
                        verbose_name='Модель AI',
                    ),
                ),
            ],
        ),
    ]
