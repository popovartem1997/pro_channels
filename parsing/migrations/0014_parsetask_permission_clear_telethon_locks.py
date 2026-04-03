from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('parsing', '0013_parseditem_source_posted_at'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='parsetask',
            options={
                'permissions': [
                    ('can_clear_telethon_locks', 'Может снимать Redis-блокировки Telethon'),
                ],
                'verbose_name': 'Задача парсинга',
                'verbose_name_plural': 'Задачи парсинга',
            },
        ),
    ]
