# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0006_post_published_by'),
    ]

    operations = [
        migrations.AlterField(
            model_name='postmedia',
            name='order',
            field=models.PositiveSmallIntegerField(default=1, verbose_name='Порядок'),
        ),
    ]
