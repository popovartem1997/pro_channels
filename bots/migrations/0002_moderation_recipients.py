from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="suggestionbot",
            name="notify_owner",
            field=models.BooleanField(
                default=True,
                help_text="Отправляет заявки владельцу в личку Telegram, если владелец привязал Telegram через импорт-бота.",
                verbose_name="Уведомлять владельца (если привязан Telegram ID)",
            ),
        ),
        migrations.AddField(
            model_name="suggestionbot",
            name="custom_admin_chat_ids",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Список числовых chat_id/user_id (например, 12345678 или -100123... для чатов).",
                verbose_name="Доп. chat_id для модерации (Telegram)",
            ),
        ),
        migrations.AddField(
            model_name="suggestionbot",
            name="moderators",
            field=models.ManyToManyField(
                blank=True,
                help_text="Выбранные пользователи получат заявки в Telegram, если у них привязан Telegram ID.",
                related_name="moderated_suggestion_bots",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Модераторы (сайт)",
            ),
        ),
    ]

