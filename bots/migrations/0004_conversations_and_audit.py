from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0003_suggestionbot_channel"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(db_index=True, max_length=100, verbose_name="Действие")),
                ("object_type", models.CharField(blank=True, max_length=100, verbose_name="Тип объекта")),
                ("object_id", models.CharField(blank=True, max_length=100, verbose_name="ID объекта")),
                ("data", models.JSONField(blank=True, default=dict, verbose_name="Данные")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_logs", to=settings.AUTH_USER_MODEL, verbose_name="Кто")),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_logs_owner", to=settings.AUTH_USER_MODEL, verbose_name="Владелец")),
            ],
            options={
                "verbose_name": "Аудит-лог",
                "verbose_name_plural": "Аудит-лог",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="BotConversation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("platform_user_id", models.CharField(db_index=True, max_length=100, verbose_name="ID пользователя на платформе")),
                ("platform_username", models.CharField(blank=True, max_length=100, verbose_name="Username")),
                ("display_name", models.CharField(blank=True, max_length=200, verbose_name="Отображаемое имя")),
                ("status", models.CharField(db_index=True, default="open", max_length=20, verbose_name="Статус")),
                ("last_message_at", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="Последнее сообщение")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("bot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conversations", to="bots.suggestionbot", verbose_name="Бот")),
            ],
            options={
                "verbose_name": "Диалог (предложка)",
                "verbose_name_plural": "Диалоги (предложка)",
                "ordering": ["-last_message_at", "-created_at"],
                "unique_together": {("bot", "platform_user_id")},
            },
        ),
        migrations.CreateModel(
            name="BotConversationMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("direction", models.CharField(choices=[("in", "Входящее"), ("out", "Исходящее")], max_length=10, verbose_name="Направление")),
                ("text", models.TextField(blank=True, verbose_name="Текст")),
                ("raw_data", models.JSONField(blank=True, default=dict, verbose_name="Сырые данные")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="bots.botconversation", verbose_name="Диалог")),
                ("sender_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bot_conversation_messages", to=settings.AUTH_USER_MODEL, verbose_name="Отправитель (сайт)")),
            ],
            options={
                "verbose_name": "Сообщение диалога (предложка)",
                "verbose_name_plural": "Сообщения диалога (предложка)",
                "ordering": ["created_at"],
            },
        ),
    ]

