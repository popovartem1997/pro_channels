from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0013_channelinterestingfacts'),
    ]

    operations = [
        migrations.AddField(
            model_name='historyimportrun',
            name='celery_task_id',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='ID задачи Celery'),
        ),
    ]
