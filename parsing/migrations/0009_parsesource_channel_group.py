# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0007_channelgroup_channel_channel_group"),
        ("parsing", "0008_merge_0006_0007"),
    ]

    operations = [
        migrations.AddField(
            model_name="parsesource",
            name="channel_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="parse_sources",
                to="channels.channelgroup",
                verbose_name="Группа каналов (проект)",
            ),
        ),
    ]

