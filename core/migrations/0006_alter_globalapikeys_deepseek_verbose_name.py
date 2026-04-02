# After RenameField, verbose_name stayed OPENAI_* in migration state; model uses DEEPSEEK_*.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_rename_openai_key_to_deepseek'),
    ]

    operations = [
        migrations.AlterField(
            model_name='globalapikeys',
            name='deepseek_api_key_enc',
            field=models.TextField(blank=True, verbose_name='DEEPSEEK_API_KEY (enc)'),
        ),
    ]
