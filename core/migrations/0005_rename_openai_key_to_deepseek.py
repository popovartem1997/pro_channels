from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_remove_tg_import_bot_token'),
    ]

    operations = [
        migrations.RenameField(
            model_name='globalapikeys',
            old_name='openai_api_key_enc',
            new_name='deepseek_api_key_enc',
        ),
    ]
