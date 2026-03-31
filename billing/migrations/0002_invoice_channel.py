from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0001_initial"),
        ("channels", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="channel",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="invoices",
                to="channels.channel",
                verbose_name="Канал (если счёт за подписку)",
            ),
        ),
    ]

