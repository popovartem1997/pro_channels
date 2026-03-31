from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0002_tg_import_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='published_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='published_posts',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Опубликовал (сайт)',
            ),
        ),
    ]

