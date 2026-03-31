from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("channels", "0001_initial"),
        ("parsing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="parsesource",
            name="channel",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="parse_sources",
                to="channels.channel",
                verbose_name="Канал (куда парсим)",
            ),
        ),
        migrations.AddField(
            model_name="parsekeyword",
            name="channel",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="parse_keywords",
                to="channels.channel",
                verbose_name="Канал (куда парсим)",
            ),
        ),
    ]

