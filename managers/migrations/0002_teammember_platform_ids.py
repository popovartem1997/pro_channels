# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("managers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="teammember",
            name="telegram_user_id",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="Telegram ID менеджера"),
        ),
        migrations.AddField(
            model_name="teammember",
            name="max_user_id",
            field=models.CharField(blank=True, default="", max_length=100, verbose_name="MAX ID менеджера"),
        ),
    ]

