from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="GlobalApiKeys",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("openai_api_key_enc", models.TextField(blank=True, verbose_name="OPENAI_API_KEY (enc)")),
                ("ai_rewrite_enabled", models.BooleanField(default=False, verbose_name="AI_REWRITE_ENABLED")),
                ("tbank_terminal_key_enc", models.TextField(blank=True, verbose_name="TBANK_TERMINAL_KEY (enc)")),
                ("tbank_secret_key_enc", models.TextField(blank=True, verbose_name="TBANK_SECRET_KEY (enc)")),
                ("vk_ord_access_token_enc", models.TextField(blank=True, verbose_name="VK_ORD_ACCESS_TOKEN (enc)")),
                ("vk_ord_cabinet_id", models.CharField(blank=True, max_length=100, verbose_name="VK_ORD_CABINET_ID")),
                ("telegram_api_id", models.CharField(blank=True, max_length=50, verbose_name="TELEGRAM_API_ID")),
                ("telegram_api_hash_enc", models.TextField(blank=True, verbose_name="TELEGRAM_API_HASH (enc)")),
                ("vk_parse_access_token_enc", models.TextField(blank=True, verbose_name="VK_PARSE_ACCESS_TOKEN (enc)")),
                ("tg_import_bot_token_enc", models.TextField(blank=True, verbose_name="TG_IMPORT_BOT_TOKEN (enc)")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Глобальные ключи API",
                "verbose_name_plural": "Глобальные ключи API",
            },
        ),
    ]

