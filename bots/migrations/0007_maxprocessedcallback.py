from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bots', '0006_suggestionbot_webhook_secret'),
    ]

    operations = [
        migrations.CreateModel(
            name='MaxProcessedCallback',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('callback_id', models.CharField(db_index=True, max_length=200, verbose_name='callback_id')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('bot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='max_processed_callbacks', to='bots.suggestionbot', verbose_name='Бот')),
            ],
            options={
                'verbose_name': 'MAX: обработанный callback',
                'verbose_name_plural': 'MAX: обработанные callback',
                'ordering': ['-created_at'],
                'unique_together': {('bot', 'callback_id')},
            },
        ),
    ]

