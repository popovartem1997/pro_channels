from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0008_channel_ord_pad_external_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoryImportRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'В очереди'), ('running', 'В работе'), ('done', 'Готово'), ('error', 'Ошибка'), ('cancelled', 'Остановлено')], db_index=True, default='pending', max_length=20)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('progress_json', models.JSONField(default=dict)),
                ('error_message', models.TextField(blank=True)),
                ('cancel_requested', models.BooleanField(db_index=True, default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='history_import_runs', to='accounts.user', verbose_name='Запустил')),
                ('source_channel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history_import_runs_as_source', to='channels.channel', verbose_name='Источник (Telegram)')),
                ('target_channel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history_import_runs_as_target', to='channels.channel', verbose_name='Цель (MAX)')),
            ],
            options={
                'verbose_name': 'Импорт истории TG→MAX',
                'verbose_name_plural': 'Импорты истории TG→MAX',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='historyimportrun',
            index=models.Index(fields=['source_channel', 'target_channel', 'status'], name='channels_hi_source__b7b41a_idx'),
        ),
    ]

