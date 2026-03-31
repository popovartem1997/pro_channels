from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0004_ads_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="channel",
            name="tg_chat_id",
            field=models.CharField(blank=True, max_length=100, verbose_name="Telegram ID канала"),
        ),
    ]

