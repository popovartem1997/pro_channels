"""
Парсинг каналов по ключевым словам.

ParseSource — источник (канал/группа) для парсинга.
ParseKeyword — ключевые слова по которым фильтруем.
ParseTask — задача парсинга (запуск, результат).
ParsedItem — найденный контент.
"""
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class ParseSource(models.Model):
    """Источник для парсинга — Telegram канал, VK группа, Дзен и т.д."""
    PLATFORM_TELEGRAM = 'telegram'
    PLATFORM_VK = 'vk'
    PLATFORM_MAX = 'max'
    PLATFORM_DZEN = 'dzen'
    PLATFORM_RSS = 'rss'
    PLATFORM_CHOICES = [
        (PLATFORM_TELEGRAM, 'Telegram'),
        (PLATFORM_VK, 'ВКонтакте'),
        (PLATFORM_MAX, 'MAX'),
        (PLATFORM_DZEN, 'Яндекс Дзен'),
        (PLATFORM_RSS, 'RSS-лента'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='parse_sources', verbose_name='Владелец')
    channel = models.ForeignKey(
        'channels.Channel', on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='parse_sources', verbose_name='Канал (куда парсим)'
    )
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, verbose_name='Платформа')
    name = models.CharField(max_length=255, verbose_name='Название')
    source_id = models.CharField(max_length=255, verbose_name='ID / URL источника',
        help_text='Для TG: @channel_name, для VK: club12345, для RSS: URL ленты')
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Источник парсинга'
        verbose_name_plural = 'Источники парсинга'

    def __str__(self):
        return f'{self.name} ({self.get_platform_display()})'


class ParseKeyword(models.Model):
    """Ключевое слово для фильтрации при парсинге."""
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='parse_keywords', verbose_name='Владелец')
    channel = models.ForeignKey(
        'channels.Channel', on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='parse_keywords', verbose_name='Канал (куда парсим)'
    )
    keyword = models.CharField(max_length=255, verbose_name='Ключевое слово / фраза')
    sources = models.ManyToManyField(ParseSource, related_name='keywords', verbose_name='Источники')
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Ключевое слово'
        verbose_name_plural = 'Ключевые слова'

    def __str__(self):
        return self.keyword


class ParsedItem(models.Model):
    """Найденный контент при парсинге."""
    STATUS_NEW = 'new'
    STATUS_USED = 'used'
    STATUS_IGNORED = 'ignored'
    STATUS_CHOICES = [
        (STATUS_NEW, 'Новый'),
        (STATUS_USED, 'Использован'),
        (STATUS_IGNORED, 'Отклонён'),
    ]

    keyword = models.ForeignKey(ParseKeyword, on_delete=models.CASCADE, related_name='items', verbose_name='Ключевое слово')
    source = models.ForeignKey(ParseSource, on_delete=models.CASCADE, related_name='items', verbose_name='Источник')
    text = models.TextField(verbose_name='Найденный текст')
    original_url = models.URLField(blank=True, verbose_name='Ссылка на оригинал')
    platform_id = models.CharField(max_length=200, blank=True, verbose_name='ID на платформе')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW, verbose_name='Статус')
    ai_rewrite = models.TextField(blank=True, verbose_name='Версия от AI')
    found_at = models.DateTimeField(auto_now_add=True, verbose_name='Найдено')

    class Meta:
        verbose_name = 'Найденный материал'
        verbose_name_plural = 'Найденные материалы'
        ordering = ['-found_at']
        unique_together = ('source', 'platform_id')

    def __str__(self):
        return f'{self.source.name}: {self.text[:60]}'


class ParseTask(models.Model):
    """Задача планового парсинга (запускается по расписанию через Celery Beat)."""
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='parse_tasks', verbose_name='Владелец')
    name = models.CharField('Название', max_length=255)
    sources = models.ManyToManyField(ParseSource, verbose_name='Источники')
    keywords = models.ManyToManyField(ParseKeyword, verbose_name='Ключевые слова')
    schedule_cron = models.CharField('Cron расписание', max_length=100, default='0 */6 * * *',
        help_text='Например: 0 */6 * * * — каждые 6 часов')
    is_active = models.BooleanField('Активна', default=True)
    last_run_at = models.DateTimeField('Последний запуск', null=True, blank=True)
    items_found_total = models.PositiveIntegerField('Найдено всего', default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Задача парсинга'
        verbose_name_plural = 'Задачи парсинга'

    def __str__(self):
        return self.name


class AIRewriteJob(models.Model):
    """Задача генерации/рерайта текста через нейросеть (OpenAI)."""
    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_DONE = 'done'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_PROCESSING, 'Обрабатывается'),
        (STATUS_DONE, 'Готово'),
        (STATUS_FAILED, 'Ошибка'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ai_rewrite_jobs', verbose_name='Автор')
    parsed_item = models.ForeignKey(ParsedItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ai_jobs', verbose_name='Источник (парсинг)')
    original_text = models.TextField('Исходный текст')
    result_text = models.TextField('Результат AI', blank=True)
    prompt = models.TextField('Кастомный промпт', blank=True)
    model_name = models.CharField('Модель AI', max_length=100, default='gpt-4o-mini')
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    error = models.TextField('Ошибка', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField('Завершено', null=True, blank=True)

    class Meta:
        verbose_name = 'Задача AI рерайта'
        verbose_name_plural = 'Задачи AI рерайта'
        ordering = ['-created_at']

    def __str__(self):
        return f'AI рерайт #{self.pk} ({self.get_status_display()})'
