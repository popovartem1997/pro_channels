from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0015_historyimportrun_celery_task_id'),
    ]

    operations = [
        migrations.AlterField(
            model_name='historyimportrun',
            name='celery_task_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Идентификатор задачи в брокере (для диагностики очереди).',
                max_length=255,
                verbose_name='ID задачи Celery',
            ),
        ),
    ]
