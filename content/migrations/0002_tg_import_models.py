from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TelegramImportLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("telegram_user_id", models.BigIntegerField(blank=True, null=True, unique=True)),
                ("code", models.CharField(max_length=32, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("linked_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="tg_import_link", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Telegram link (import)",
                "verbose_name_plural": "Telegram links (import)",
            },
        ),
        migrations.CreateModel(
            name="TelegramImportedMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.TextField(blank=True)),
                ("entities", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tg_imported_messages", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Telegram imported message",
                "verbose_name_plural": "Telegram imported messages",
                "ordering": ["-created_at"],
            },
        ),
    ]

