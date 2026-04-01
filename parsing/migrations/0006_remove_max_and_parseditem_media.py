# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("parsing", "0005_alter_parsesource_platform_choices"),
    ]

    operations = [
        migrations.AlterField(
            model_name="parsesource",
            name="platform",
            field=models.CharField(
                choices=[
                    ("telegram", "Telegram"),
                    ("vk", "ВКонтакте"),
                    ("dzen", "Яндекс Дзен"),
                    ("rss", "RSS-лента"),
                ],
                max_length=20,
                verbose_name="Платформа",
            ),
        ),
        migrations.AddField(
            model_name="parseditem",
            name="media",
            field=models.JSONField(blank=True, default=list, verbose_name="Медиа (список файлов/URL)"),
        ),
    ]

