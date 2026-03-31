"""
Модели для системы ботов-предложок.

SuggestionBot   — настройки конкретного бота (Telegram / VK / MAX)
Suggestion      — одна предложенная пользователем заявка
SuggestionUserStats — агрегированная статистика по пользователю (лидерборд)
"""
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class SuggestionBot(models.Model):
    """Бот-предложка, привязанный к конкретному каналу/паблику владельца."""

    PLATFORM_TELEGRAM = 'telegram'
    PLATFORM_VK = 'vk'
    PLATFORM_MAX = 'max'
    PLATFORM_CHOICES = [
        (PLATFORM_TELEGRAM, 'Telegram'),
        (PLATFORM_VK, 'ВКонтакте'),
        (PLATFORM_MAX, 'MAX'),
    ]

    owner = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='suggestion_bots',
        verbose_name='Владелец'
    )
    name = models.CharField(max_length=255, verbose_name='Название бота')
    platform = models.CharField(
        max_length=20, choices=PLATFORM_CHOICES,
        verbose_name='Платформа'
    )
    # Токен хранится зашифрованным (см. utils.py)
    bot_token_encrypted = models.TextField(verbose_name='Токен бота (зашифрован)')
    bot_username = models.CharField(
        max_length=100, blank=True,
        verbose_name='Username / Имя бота'
    )
    # Для Telegram: ID чата куда пересылаются заявки на модерацию
    admin_chat_id = models.CharField(
        max_length=100, blank=True,
        verbose_name='ID чата для модерации (Telegram)',
        help_text='Chat ID группы или канала, куда будут приходить заявки с кнопками'
    )
    # Для VK/MAX: ID группы
    group_id = models.CharField(
        max_length=100, blank=True,
        verbose_name='ID группы/сообщества (VK / MAX)'
    )
    is_active = models.BooleanField(default=True, verbose_name='Активен')

    # Настройка сообщений бота
    welcome_message = models.TextField(
        default='Привет! Отправьте мне вашу новость или предложение, и мы рассмотрим его для публикации.',
        verbose_name='Приветственное сообщение'
    )
    success_message = models.TextField(
        default='Спасибо! Ваша заявка #{tracking_id} принята и ожидает модерации.\n\nПроверить статус: /status',
        verbose_name='Сообщение об успешной отправке',
        help_text='Используйте {tracking_id} — ID заявки'
    )
    approved_message = models.TextField(
        default='Ваша заявка #{tracking_id} одобрена и будет опубликована! Спасибо за вклад.',
        verbose_name='Сообщение об одобрении',
        help_text='Используйте {tracking_id}'
    )
    rejected_message = models.TextField(
        default='Ваша заявка #{tracking_id} не прошла модерацию.\nПричина: {reason}',
        verbose_name='Сообщение об отклонении',
        help_text='Используйте {tracking_id} и {reason}'
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлён')

    class Meta:
        verbose_name = 'Бот предложки'
        verbose_name_plural = 'Боты предложки'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.get_platform_display()})'

    def set_token(self, raw_token: str):
        """Зашифровать и сохранить токен."""
        from .utils import encrypt_token
        self.bot_token_encrypted = encrypt_token(raw_token)

    def get_token(self) -> str:
        """Получить расшифрованный токен."""
        from .utils import decrypt_token
        return decrypt_token(self.bot_token_encrypted)


class Suggestion(models.Model):
    """Одна заявка, отправленная пользователем через бот."""

    # Статусы модерации
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_PUBLISHED = 'published'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'На модерации'),
        (STATUS_APPROVED, 'Одобрено'),
        (STATUS_REJECTED, 'Отклонено'),
        (STATUS_PUBLISHED, 'Опубликовано'),
    ]
    STATUS_EMOJI = {
        STATUS_PENDING: '⏳',
        STATUS_APPROVED: '✅',
        STATUS_REJECTED: '❌',
        STATUS_PUBLISHED: '📢',
    }

    # Типы контента
    CONTENT_TEXT = 'text'
    CONTENT_PHOTO = 'photo'
    CONTENT_VIDEO = 'video'
    CONTENT_DOCUMENT = 'document'
    CONTENT_AUDIO = 'audio'
    CONTENT_VOICE = 'voice'
    CONTENT_MIXED = 'mixed'
    CONTENT_CHOICES = [
        (CONTENT_TEXT, 'Текст'),
        (CONTENT_PHOTO, 'Фото'),
        (CONTENT_VIDEO, 'Видео'),
        (CONTENT_DOCUMENT, 'Документ'),
        (CONTENT_AUDIO, 'Аудио'),
        (CONTENT_VOICE, 'Голосовое'),
        (CONTENT_MIXED, 'Смешанное'),
    ]

    tracking_id = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False,
        verbose_name='ID отслеживания'
    )
    bot = models.ForeignKey(
        SuggestionBot, on_delete=models.CASCADE,
        related_name='suggestions',
        verbose_name='Бот'
    )

    # Информация об отправителе
    platform_user_id = models.CharField(max_length=100, verbose_name='ID пользователя')
    platform_username = models.CharField(max_length=100, blank=True, verbose_name='Username')
    platform_first_name = models.CharField(max_length=100, blank=True, verbose_name='Имя')
    platform_last_name = models.CharField(max_length=100, blank=True, verbose_name='Фамилия')

    # Контент
    content_type = models.CharField(
        max_length=20, choices=CONTENT_CHOICES,
        verbose_name='Тип контента'
    )
    text = models.TextField(blank=True, verbose_name='Текст')
    # ID файлов платформы (file_id в Telegram, attachment в VK и т.д.)
    media_file_ids = models.JSONField(default=list, verbose_name='ID медиафайлов')
    # Полный JSON от платформы — для дебага и будущих расширений
    raw_data = models.JSONField(default=dict, verbose_name='Сырые данные от платформы')

    # Модерация
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name='Статус'
    )
    moderated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='moderated_suggestions',
        verbose_name='Модератор'
    )
    moderated_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата модерации')
    rejection_reason = models.TextField(blank=True, verbose_name='Причина отклонения')
    moderator_note = models.TextField(blank=True, verbose_name='Внутренняя заметка модератора')

    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата подачи')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Предложение'
        verbose_name_plural = 'Предложения'
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['bot', 'status']),
            models.Index(fields=['platform_user_id', 'bot']),
        ]

    def __str__(self):
        return f'Заявка #{self.short_tracking_id} — {self.get_status_display()}'

    @property
    def short_tracking_id(self) -> str:
        """Первые 8 символов UUID — удобны для показа пользователю."""
        return str(self.tracking_id)[:8].upper()

    @property
    def sender_display(self) -> str:
        if self.platform_username:
            return f'@{self.platform_username}'
        name = ' '.join(filter(None, [self.platform_first_name, self.platform_last_name]))
        return name or f'ID:{self.platform_user_id}'

    @property
    def status_emoji(self) -> str:
        return self.STATUS_EMOJI.get(self.status, '?')

    def approve(self, moderator=None):
        """Одобрить заявку и обновить статистику."""
        self.status = self.STATUS_APPROVED
        self.moderated_at = timezone.now()
        if moderator:
            self.moderated_by = moderator
        self.save(update_fields=['status', 'moderated_at', 'moderated_by'])
        self._update_stats(approved=True)

    def reject(self, reason: str = '', moderator=None):
        """Отклонить заявку и обновить статистику."""
        self.status = self.STATUS_REJECTED
        self.moderated_at = timezone.now()
        self.rejection_reason = reason
        if moderator:
            self.moderated_by = moderator
        self.save(update_fields=['status', 'moderated_at', 'moderated_by', 'rejection_reason'])
        self._update_stats(rejected=True)

    def _update_stats(self, approved=False, rejected=False):
        stats = SuggestionUserStats.objects.filter(
            bot=self.bot, platform_user_id=self.platform_user_id
        ).first()
        if not stats:
            return
        if stats.pending > 0:
            stats.pending -= 1
        if approved:
            stats.approved += 1
        if rejected:
            stats.rejected += 1
        stats.save(update_fields=['pending', 'approved', 'rejected'])


class SuggestionUserStats(models.Model):
    """
    Агрегированная статистика пользователя по конкретному боту.
    Используется для лидерборда на сайте и в самом боте (/status).
    """
    bot = models.ForeignKey(
        SuggestionBot, on_delete=models.CASCADE,
        related_name='user_stats',
        verbose_name='Бот'
    )
    platform_user_id = models.CharField(max_length=100, verbose_name='ID пользователя')
    platform_username = models.CharField(max_length=100, blank=True, verbose_name='Username')
    display_name = models.CharField(max_length=200, blank=True, verbose_name='Отображаемое имя')

    total = models.PositiveIntegerField(default=0, verbose_name='Всего предложений')
    approved = models.PositiveIntegerField(default=0, verbose_name='Одобрено')
    rejected = models.PositiveIntegerField(default=0, verbose_name='Отклонено')
    pending = models.PositiveIntegerField(default=0, verbose_name='На модерации')
    published = models.PositiveIntegerField(default=0, verbose_name='Опубликовано')

    last_submission = models.DateTimeField(null=True, blank=True, verbose_name='Последняя заявка')

    class Meta:
        verbose_name = 'Статистика пользователя'
        verbose_name_plural = 'Статистика пользователей'
        unique_together = ('bot', 'platform_user_id')
        ordering = ['-approved', '-total']

    def __str__(self):
        return f'{self.display_name or self.platform_user_id} — {self.total} заявок'
