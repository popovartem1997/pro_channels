from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("parsing", "0004_alter_parsekeyword_channel_alter_parsesource_channel"),
    ]

    operations = [
        migrations.AlterField(
            model_name="parsesource",
            name="platform",
            field=models.CharField(
                choices=[
                    ("telegram", "Telegram"),
                    ("vk", "ВКонтакте"),
                    ("max", "MAX"),
                    ("dzen", "Яндекс Дзен"),
                    ("rss", "RSS-лента"),
                ],
                max_length=20,
                verbose_name="Платформа",
            ),
        ),
    ]

