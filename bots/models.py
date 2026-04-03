"""
Модели для системы ботов-предложок.

SuggestionBot   — настройки конкретного бота (Telegram / VK / MAX)
Suggestion      — одна предложенная пользователем заявка
SuggestionUserStats — агрегированная статистика по пользователю (лидерборд)
"""
import logging
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()
logger = logging.getLogger(__name__)


def telegram_user_id_for_moderation_recipient(owner_id: int, user) -> int | None:
    """
    Числовой Telegram user_id для уведомлений о предложках.
    Сначала профиль (accounts.User), затем карточка команды (владелец или менеджер).
    """
    tid = getattr(user, 'telegram_user_id', None)
    if tid is not None:
        try:
            i = int(tid)
            if i > 0:
                return i
        except (TypeError, ValueError):
            pass
    try:
        from managers.models import TeamMember

        tm = TeamMember.objects.filter(
            owner_id=int(owner_id),
            member_id=user.pk,
            is_active=True,
        ).first()
        if tm and tm.telegram_user_id is not None:
            try:
                i = int(tm.telegram_user_id)
                if i > 0:
                    return i
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    return None


def max_user_id_str_for_moderation_recipient(owner_id: int, user) -> str:
    """MAX user_id: профиль, затем карточка команды."""
    u = (getattr(user, 'max_user_id', None) or '').strip()
    if u:
        return u
    try:
        from managers.models import TeamMember

        tm = TeamMember.objects.filter(
            owner_id=int(owner_id),
            member_id=user.pk,
            is_active=True,
        ).first()
        if tm:
            return (tm.max_user_id or '').strip()
    except Exception:
        pass
    return ''


class SuggestionBot(models.Model):
    """Бот-предложка, привязанный к одной или нескольким группам каналов (паблик в разных соцсетях)."""

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
    channel_groups = models.ManyToManyField(
        'channels.ChannelGroup',
        blank=True,
        related_name='suggestion_bots',
        verbose_name='Группы каналов',
        help_text='Предложки и черновики постов привязываются ко всем активным каналам выбранных групп.',
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
    webhook_secret = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Webhook secret (Telegram)',
        help_text='Секретный токен для заголовка X-Telegram-Bot-Api-Secret-Token (настраивается при setWebhook).',
    )
    notify_owner = models.BooleanField(
        default=True,
        verbose_name='Уведомлять владельца (если привязан Telegram ID)',
        help_text='Отправляет заявки владельцу в личку Telegram, если владелец привязал Telegram через импорт-бота.'
    )
    moderators = models.ManyToManyField(
        User,
        blank=True,
        related_name='moderated_suggestion_bots',
        verbose_name='Модераторы (сайт)',
        help_text='Выбранные пользователи получат заявки в Telegram, если у них привязан Telegram ID.'
    )
    custom_admin_chat_ids = models.JSONField(
        default=list,
        blank=True,
        verbose_name='Доп. chat_id для модерации (Telegram)',
        help_text='Список числовых chat_id/user_id (например, 12345678 или -100123... для чатов).'
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

    def target_channel_ids(self) -> list[int]:
        """Активные каналы из всех привязанных групп (без дубликатов), порядок стабильный."""
        from channels.models import Channel

        seen: set[int] = set()
        ordered: list[int] = []
        for g in self.channel_groups.all().order_by('pk'):
            for pk in (
                Channel.objects.filter(channel_group=g, is_active=True)
                .order_by('pk')
                .values_list('pk', flat=True)
            ):
                if pk not in seen:
                    seen.add(pk)
                    ordered.append(pk)
        return ordered

    def representative_channel(self):
        """
        Канал для контактов админа и подсказок в UI: сначала совпадение по платформе бота, иначе первый активный.
        """
        from channels.models import Channel

        collected: list = []
        seen: set[int] = set()
        for g in self.channel_groups.all().order_by('pk'):
            for ch in Channel.objects.filter(channel_group=g, is_active=True).order_by('pk'):
                if ch.pk in seen:
                    continue
                seen.add(ch.pk)
                collected.append(ch)
        if not collected:
            return None
        for ch in collected:
            if ch.platform == self.platform:
                return ch
        return collected[0]

    def display_channels(self):
        """Активные каналы из всех групп (для бейджей в ленте), без дубликатов."""
        seen: set[int] = set()
        out: list = []
        for g in self.channel_groups.all().order_by('pk'):
            for ch in g.channels.filter(is_active=True).order_by('name', 'pk'):
                if ch.pk in seen:
                    continue
                seen.add(ch.pk)
                out.append(ch)
        return out

    def _telegram_user_id_for_recipient(self, user) -> int | None:
        """Telegram user_id: профиль или карточка команды (одна логика для владельца и менеджера)."""
        return telegram_user_id_for_moderation_recipient(int(self.owner_id), user)

    def _max_user_id_str_for_recipient(self, user) -> str:
        """MAX user_id: профиль или карточка команды."""
        return max_user_id_str_for_moderation_recipient(int(self.owner_id), user)

    def get_moderation_chat_ids(self) -> list[str]:
        """
        Список chat_id для Telegram: выбранные в настройках бота пользователи (профиль / команда)
        плюс admin_chat_id и custom_admin_chat_ids (группа/доп. чаты).
        """
        ids: list[str] = []

        def _add(val):
            if val is None:
                return
            s = str(val).strip()
            if not s:
                return
            if s not in ids:
                ids.append(s)

        mod_users = list(self.moderators.all())
        mod_resolved = 0
        for u in mod_users:
            tid = self._telegram_user_id_for_recipient(u)
            if tid is not None:
                _add(str(tid))
                mod_resolved += 1

        _add(self.admin_chat_id)
        for x in (self.custom_admin_chat_ids or []):
            _add(x)

        if mod_users and mod_resolved == 0 and not (self.admin_chat_id or '').strip() and not (self.custom_admin_chat_ids or []):
            logger.warning(
                'SuggestionBot id=%s: в «Кому слать модерацию» выбраны пользователи, но ни у кого не найден '
                'Telegram user ID (профиль/команда) и не задан чат модерации — уведомления в Telegram не уйдут.',
                self.pk,
            )

        return ids

    def get_moderation_max_dm_ids(self) -> list[str]:
        """MAX: user_id для личных сообщений выбранным получателям модерации."""
        out: list[str] = []
        seen: set[str] = set()
        mod_users = list(self.moderators.all())
        for u in mod_users:
            s = self._max_user_id_str_for_recipient(u)
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        if mod_users and not out and not (self.admin_chat_id or '').strip():
            logger.warning(
                'SuggestionBot id=%s: для MAX выбраны получатели модерации, но ни у кого нет MAX user ID '
                'и не задан admin_chat_id — личные уведомления не уйдут.',
                self.pk,
            )
        return out


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


class SuggestionStoredMedia(models.Model):
    """
    Файлы вложений MAX (и при необходимости других платформ), сохранённые при приёме предложки.
    При создании поста из предложки копируются в PostMedia без повторного скачивания с CDN.
    """
    MEDIA_PHOTO = 'photo'
    MEDIA_VIDEO = 'video'
    MEDIA_DOCUMENT = 'document'
    MEDIA_CHOICES = [
        (MEDIA_PHOTO, 'Фото'),
        (MEDIA_VIDEO, 'Видео'),
        (MEDIA_DOCUMENT, 'Документ'),
    ]

    suggestion = models.ForeignKey(
        Suggestion, on_delete=models.CASCADE, related_name='stored_media', verbose_name='Предложение'
    )
    file = models.FileField(upload_to='suggestion_stored/%Y/%m/', max_length=500, verbose_name='Файл')
    media_type = models.CharField(max_length=20, choices=MEDIA_CHOICES, default=MEDIA_PHOTO, verbose_name='Тип')
    attachment_key = models.CharField(
        max_length=300, db_index=True,
        verbose_name='Ключ вложения',
        help_text='Токен MAX или синтетический ключ для дедупликации',
    )
    order = models.PositiveSmallIntegerField(default=0, verbose_name='Порядок')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Сохранённое медиа предложки'
        verbose_name_plural = 'Сохранённые медиа предложки'
        ordering = ['order', 'pk']
        unique_together = ('suggestion', 'attachment_key')

    def __str__(self):
        return f'{self.suggestion_id} #{self.order} ({self.media_type})'


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


class BotConversation(models.Model):
    """Диалог подписчика с менеджерами по конкретному боту (MVP: текст)."""
    bot = models.ForeignKey(SuggestionBot, on_delete=models.CASCADE, related_name='conversations', verbose_name='Бот')
    platform_user_id = models.CharField(max_length=100, db_index=True, verbose_name='ID пользователя на платформе')
    platform_username = models.CharField(max_length=100, blank=True, verbose_name='Username')
    display_name = models.CharField(max_length=200, blank=True, verbose_name='Отображаемое имя')
    status = models.CharField(max_length=20, default='open', db_index=True, verbose_name='Статус')
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name='Последнее сообщение')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Диалог (предложка)'
        verbose_name_plural = 'Диалоги (предложка)'
        ordering = ['-last_message_at', '-created_at']
        unique_together = ('bot', 'platform_user_id')

    def __str__(self):
        return f'{self.bot_id}:{self.platform_user_id} ({self.status})'


class BotConversationMessage(models.Model):
    conversation = models.ForeignKey(BotConversation, on_delete=models.CASCADE, related_name='messages', verbose_name='Диалог')
    direction = models.CharField(max_length=10, choices=[('in', 'Входящее'), ('out', 'Исходящее')], verbose_name='Направление')
    sender_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='bot_conversation_messages', verbose_name='Отправитель (сайт)'
    )
    text = models.TextField(blank=True, verbose_name='Текст')
    raw_data = models.JSONField(default=dict, blank=True, verbose_name='Сырые данные')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Сообщение диалога (предложка)'
        verbose_name_plural = 'Сообщения диалога (предложка)'
        ordering = ['created_at']


class AuditLog(models.Model):
    """Аудит-лог действий в кабинете (MVP: правки ботов/подписок/каналов)."""
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='audit_logs', verbose_name='Кто')
    owner = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='audit_logs_owner', verbose_name='Владелец')
    action = models.CharField(max_length=100, db_index=True, verbose_name='Действие')
    object_type = models.CharField(max_length=100, blank=True, verbose_name='Тип объекта')
    object_id = models.CharField(max_length=100, blank=True, verbose_name='ID объекта')
    data = models.JSONField(default=dict, blank=True, verbose_name='Данные')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Аудит-лог'
        verbose_name_plural = 'Аудит-лог'
        ordering = ['-created_at']


class MaxProcessedCallback(models.Model):
    """
    Дедупликация callback-событий MAX (нажатия кнопок).
    Нужна, когда вебхук обрабатывается несколькими воркерами/инстансами без общего Redis.
    """

    bot = models.ForeignKey(SuggestionBot, on_delete=models.CASCADE, related_name='max_processed_callbacks', verbose_name='Бот')
    callback_id = models.CharField(max_length=200, db_index=True, verbose_name='callback_id')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'MAX: обработанный callback'
        verbose_name_plural = 'MAX: обработанные callback'
        unique_together = ('bot', 'callback_id')
        ordering = ['-created_at']
