# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bots', '0004_conversations_and_audit'),
    ]

    operations = [
        migrations.CreateModel(
            name='SuggestionStoredMedia',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(max_length=500, upload_to='suggestion_stored/%Y/%m/', verbose_name='Файл')),
                ('media_type', models.CharField(
                    choices=[('photo', 'Фото'), ('video', 'Видео'), ('document', 'Документ')],
                    default='photo', max_length=20, verbose_name='Тип',
                )),
                ('attachment_key', models.CharField(db_index=True, help_text='Токен MAX или синтетический ключ для дедупликации', max_length=300, verbose_name='Ключ вложения')),
                ('order', models.PositiveSmallIntegerField(default=0, verbose_name='Порядок')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('suggestion', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stored_media', to='bots.suggestion', verbose_name='Предложение')),
            ],
            options={
                'verbose_name': 'Сохранённое медиа предложки',
                'verbose_name_plural': 'Сохранённые медиа предложки',
                'ordering': ['order', 'pk'],
                'unique_together': {('suggestion', 'attachment_key')},
            },
        ),
    ]
