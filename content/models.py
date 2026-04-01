"""
Post — один пост для публикации в один или несколько каналов.
PostMedia — медиафайл (фото/видео/документ) прикреплённый к посту.
PublishResult — результат публикации поста в конкретный канал.
"""
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class Post(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_SCHEDULED = 'scheduled'
    STATUS_PUBLISHING = 'publishing'
    STATUS_PUBLISHED = 'published'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_SCHEDULED, 'Запланирован'),
        (STATUS_PUBLISHING, 'Публикуется'),
        (STATUS_PUBLISHED, 'Опубликован'),
        (STATUS_FAILED, 'Ошибка'),
    ]

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts', verbose_name='Автор')
    published_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='published_posts',
        verbose_name='Опубликовал (сайт)',
    )
    channels = models.ManyToManyField('channels.Channel', related_name='posts', verbose_name='Каналы для публикации')

    text = models.TextField(verbose_name='Текст поста')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True, verbose_name='Статус')

    # Планирование
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name='Запланировано на')
    published_at = models.DateTimeField(null=True, blank=True, verbose_name='Опубликовано в')

    # Повторная публикация
    REPEAT_NONE = 'none'
    REPEAT_DAILY = 'daily'
    REPEAT_WEEKLY = 'weekly'
    REPEAT_INTERVAL = 'interval'
    REPEAT_CHOICES = [
        (REPEAT_NONE, 'Без повтора'),
        (REPEAT_DAILY, 'Ежедневно'),
        (REPEAT_WEEKLY, 'Еженедельно'),
        (REPEAT_INTERVAL, 'Через N дней'),
    ]
    repeat_enabled = models.BooleanField(default=False, verbose_name='Повторять публикацию')
    repeat_type = models.CharField(max_length=20, choices=REPEAT_CHOICES, default=REPEAT_NONE, verbose_name='Тип повтора')
    repeat_interval_days = models.PositiveIntegerField(default=3, verbose_name='Интервал повтора (дней)')
    repeat_count = models.PositiveIntegerField(default=0, verbose_name='Кол-во повторов (0=∞)')
    repeat_end_date = models.DateField(null=True, blank=True, verbose_name='Дата окончания повтора')

    # TG Premium Emoji
    has_premium_emoji = models.BooleanField(default=False, verbose_name='Содержит TG Premium Emoji')
    tg_entities = models.JSONField(default=list, verbose_name='TG entities (premium emoji)')

    # ВК ОРД маркировка
    ord_label = models.CharField(max_length=500, blank=True, verbose_name='Текст метки ОРД (Реклама)')
    ord_token = models.CharField(max_length=200, blank=True, verbose_name='Токен ОРД')

    # Настройки публикации
    disable_notification = models.BooleanField(default=False, verbose_name='Тихая публикация (TG)')
    pin_message = models.BooleanField(default=False, verbose_name='Закрепить сообщение')

    # Из предложки
    suggestion = models.ForeignKey(
        'bots.Suggestion', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='posts',
        verbose_name='Источник (предложка)'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Пост'
        verbose_name_plural = 'Посты'
        ordering = ['-created_at']

    def __str__(self):
        preview = self.text[:60] + '…' if len(self.text) > 60 else self.text
        return f'{preview} ({self.get_status_display()})'

    @property
    def is_ready_to_publish(self) -> bool:
        return (
            self.status == self.STATUS_SCHEDULED
            and self.scheduled_at is not None
            and self.scheduled_at <= timezone.now()
        )

    def schedule_next_repeat(self):
        """Создать следующую отложенную публикацию если включён повтор."""
        if not self.repeat_enabled or self.repeat_type == self.REPEAT_NONE:
            return

        from datetime import timedelta

        # Определяем интервал на основе типа повтора
        if self.repeat_type == self.REPEAT_DAILY:
            delta = timedelta(days=1)
        elif self.repeat_type == self.REPEAT_WEEKLY:
            delta = timedelta(weeks=1)
        elif self.repeat_type == self.REPEAT_INTERVAL:
            delta = timedelta(days=self.repeat_interval_days or 3)
        else:
            return

        next_time = (self.published_at or timezone.now()) + delta

        # Проверяем дату окончания повтора
        if self.repeat_end_date and next_time.date() > self.repeat_end_date:
            return

        # Вычисляем оставшееся количество повторов (0 = бесконечно)
        next_repeat_count = self.repeat_count
        if self.repeat_count > 0:
            next_repeat_count = self.repeat_count - 1
            if next_repeat_count < 0:
                return

        new_post = Post.objects.create(
            author=self.author,
            text=self.text,
            status=self.STATUS_SCHEDULED,
            scheduled_at=next_time,
            repeat_enabled=True,
            repeat_type=self.repeat_type,
            repeat_interval_days=self.repeat_interval_days,
            repeat_count=next_repeat_count,
            repeat_end_date=self.repeat_end_date,
            has_premium_emoji=self.has_premium_emoji,
            tg_entities=self.tg_entities,
            ord_label=self.ord_label,
            ord_token=self.ord_token,
            disable_notification=self.disable_notification,
            pin_message=self.pin_message,
        )

        # Копируем M2M каналы
        new_post.channels.set(self.channels.all())

        # Копируем медиафайлы
        for media in self.media_files.all():
            PostMedia.objects.create(
                post=new_post,
                file=media.file,
                media_type=media.media_type,
                order=media.order,
            )


class PostMedia(models.Model):
    TYPE_PHOTO = 'photo'
    TYPE_VIDEO = 'video'
    TYPE_DOCUMENT = 'document'
    TYPE_CHOICES = [
        (TYPE_PHOTO, 'Фото'),
        (TYPE_VIDEO, 'Видео'),
        (TYPE_DOCUMENT, 'Документ'),
    ]

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='media_files', verbose_name='Пост')
    file = models.FileField(upload_to='post_media/%Y/%m/', verbose_name='Файл')
    media_type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name='Тип')
    order = models.PositiveSmallIntegerField(default=1, verbose_name='Порядок')

    class Meta:
        verbose_name = 'Медиафайл поста'
        verbose_name_plural = 'Медиафайлы постов'
        ordering = ['order']

    def __str__(self):
        return f'{self.get_media_type_display()} для поста #{self.post.pk}'


class PublishResult(models.Model):
    STATUS_OK = 'ok'
    STATUS_FAIL = 'fail'
    STATUS_CHOICES = [
        (STATUS_OK, 'Успешно'),
        (STATUS_FAIL, 'Ошибка'),
    ]

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='publish_results', verbose_name='Пост')
    channel = models.ForeignKey('channels.Channel', on_delete=models.CASCADE, related_name='publish_results', verbose_name='Канал')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, verbose_name='Статус')
    platform_message_id = models.CharField(max_length=200, blank=True, verbose_name='ID сообщения на платформе')
    error_message = models.TextField(blank=True, verbose_name='Текст ошибки')
    published_at = models.DateTimeField(auto_now_add=True, verbose_name='Время публикации')

    class Meta:
        verbose_name = 'Результат публикации'
        verbose_name_plural = 'Результаты публикации'
        ordering = ['-published_at']

    def __str__(self):
        return f'Пост #{self.post.pk} → {self.channel.name}: {self.get_status_display()}'


class PostTemplate(models.Model):
    """Шаблон поста для быстрого создания."""
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_templates', verbose_name='Автор')
    name = models.CharField('Название шаблона', max_length=255)
    text = models.TextField('Текст шаблона')
    channels = models.ManyToManyField('channels.Channel', blank=True, verbose_name='Каналы по умолчанию')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Шаблон поста'
        verbose_name_plural = 'Шаблоны постов'

    def __str__(self):
        return self.name


# Telegram import (premium emoji helper)
from .models_imports import TelegramImportLink, TelegramImportedMessage  # noqa: E402,F401
