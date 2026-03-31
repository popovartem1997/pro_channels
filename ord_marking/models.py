"""
ВК ОРД маркировка рекламных постов.
"""
from django.db import models


class ORDRegistration(models.Model):
    """Регистрация рекламного материала в ВК ОРД."""
    STATUS_PENDING = 'pending'
    STATUS_REGISTERED = 'registered'
    STATUS_ERROR = 'error'
    STATUS_EXPIRED = 'expired'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_REGISTERED, 'Зарегистрирован'),
        (STATUS_ERROR, 'Ошибка'),
        (STATUS_EXPIRED, 'Истёк'),
    ]

    post = models.ForeignKey(
        'content.Post', on_delete=models.CASCADE,
        related_name='ord_registrations', verbose_name='Пост'
    )
    channel = models.ForeignKey(
        'channels.Channel', on_delete=models.CASCADE,
        related_name='ord_registrations', verbose_name='Канал'
    )
    advertiser = models.ForeignKey(
        'advertisers.Advertiser', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='Рекламодатель'
    )
    ord_token = models.CharField('Токен маркировки ОРД', max_length=500, blank=True)
    ord_id = models.CharField('ID в реестре ОРД', max_length=200, blank=True)
    label_text = models.CharField('Текст метки', max_length=500, default='Реклама')
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    registered_at = models.DateTimeField('Зарегистрировано', null=True, blank=True)
    expires_at = models.DateTimeField('Истекает', null=True, blank=True)
    error_message = models.TextField('Ошибка', blank=True)
    raw_response = models.JSONField('Ответ ВК ОРД API', default=dict)
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        verbose_name = 'ОРД регистрация'
        verbose_name_plural = 'ОРД регистрации'
        ordering = ['-created_at']

    def __str__(self):
        return f'ОРД: {self.post} → {self.channel} ({self.get_status_display()})'
