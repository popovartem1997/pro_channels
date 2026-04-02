from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parsing', '0012_alter_airerewritejob_model_name_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='parseditem',
            name='source_posted_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Дата публикации в источнике',
            ),
        ),
    ]
