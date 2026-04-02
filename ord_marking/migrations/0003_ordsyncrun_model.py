from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('ord_marking', '0002_ordregistration_erid_stats'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrdSyncRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'В очереди'), ('running', 'В работе'), ('done', 'Готово'), ('error', 'Ошибка')], db_index=True, default='pending', max_length=20)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('result', models.JSONField(default=dict)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ord_sync_runs', to='accounts.user', verbose_name='Запустил')),
            ],
            options={
                'verbose_name': 'ОРД синхронизация',
                'verbose_name_plural': 'ОРД синхронизации',
                'ordering': ['-created_at'],
            },
        ),
    ]

