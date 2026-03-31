from django.db import migrations, models
import django.db.models.deletion


def backfill_channel(apps, schema_editor):
    Channel = apps.get_model("channels", "Channel")
    ParseSource = apps.get_model("parsing", "ParseSource")
    ParseKeyword = apps.get_model("parsing", "ParseKeyword")

    # Для каждого owner проставим первый активный канал (или просто первый) во все пустые записи
    owner_ids = set(
        list(ParseSource.objects.filter(channel__isnull=True).values_list("owner_id", flat=True))
        + list(ParseKeyword.objects.filter(channel__isnull=True).values_list("owner_id", flat=True))
    )
    for owner_id in owner_ids:
        ch = Channel.objects.filter(owner_id=owner_id, is_active=True).order_by("created_at").first()
        if not ch:
            ch = Channel.objects.filter(owner_id=owner_id).order_by("created_at").first()
        if not ch:
            continue
        ParseSource.objects.filter(owner_id=owner_id, channel__isnull=True).update(channel_id=ch.id)
        ParseKeyword.objects.filter(owner_id=owner_id, channel__isnull=True).update(channel_id=ch.id)


class Migration(migrations.Migration):
    dependencies = [
        ("parsing", "0002_channel_scoping"),
    ]

    operations = [
        migrations.RunPython(backfill_channel, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="parsesource",
            name="channel",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="parse_sources",
                to="channels.channel",
                verbose_name="Канал (куда парсим)",
            ),
        ),
        migrations.AlterField(
            model_name="parsekeyword",
            name="channel",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="parse_keywords",
                to="channels.channel",
                verbose_name="Канал (куда парсим)",
            ),
        ),
    ]

