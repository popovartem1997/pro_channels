# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0007_channelgroup_channel_channel_group"),
        ("parsing", "0009_parsesource_channel_group"),
    ]

    operations = [
        migrations.AddField(
            model_name="parsekeyword",
            name="channel_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="parse_keywords",
                to="channels.channelgroup",
                verbose_name="Группа каналов (проект)",
            ),
        ),
    ]

