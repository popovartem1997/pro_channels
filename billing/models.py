"""
Биллинг: тарифы, счета, платежи, подписки на каналы.
"""
import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone


class Plan(models.Model):
    """Тарифный план."""
    PLAN_TRIAL = 'trial'
    PLAN_BASIC = 'basic'
    PLAN_CHOICES = [
        (PLAN_TRIAL, 'Пробный (30 дней)'),
        (PLAN_BASIC, 'Базовый'),
    ]

    code = models.CharField('Код', max_length=50, unique=True)
    name = models.CharField('Название', max_length=255)
    price = models.DecimalField('Цена (₽/мес)', max_digits=10, decimal_places=2, default=299.00)
    channels_limit = models.PositiveIntegerField('Кол-во пабликов', default=1)
    duration_days = models.PositiveIntegerField('Длительность (дней)', default=30)
    is_active = models.BooleanField('Активен', default=True)
    description = models.TextField('Описание', blank=True)
    features = models.JSONField('Возможности (список)', default=list)

    class Meta:
        verbose_name = 'Тариф'
        verbose_name_plural = 'Тарифы'

    def __str__(self):
        return f'{self.name} — {self.price} ₽/мес'


class Invoice(models.Model):
    """Счёт на оплату."""
    STATUS_DRAFT = 'draft'
    STATUS_SENT = 'sent'
    STATUS_PAID = 'paid'
    STATUS_CANCELLED = 'cancelled'
    STATUS_OVERDUE = 'overdue'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_SENT, 'Выставлен'),
        (STATUS_PAID, 'Оплачен'),
        (STATUS_CANCELLED, 'Отменён'),
        (STATUS_OVERDUE, 'Просрочен'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='invoices', verbose_name='Пользователь'
    )
    channel = models.ForeignKey(
        'channels.Channel', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='invoices',
        verbose_name='Канал (если счёт за подписку)'
    )
    number = models.CharField('Номер счёта', max_length=50, unique=True)
    amount = models.DecimalField('Сумма (₽)', max_digits=12, decimal_places=2)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    description = models.TextField('Описание услуги')
    due_date = models.DateField('Срок оплаты', null=True, blank=True)
    paid_at = models.DateTimeField('Оплачено', null=True, blank=True)
    tbank_payment_id = models.CharField('ID платежа TBank', max_length=200, blank=True)
    tbank_order_id = models.CharField('Номер заказа TBank', max_length=200, blank=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    pdf_file = models.FileField('PDF', upload_to='invoices/', blank=True)

    class Meta:
        verbose_name = 'Счёт'
        verbose_name_plural = 'Счета'
        ordering = ['-created_at']

    def __str__(self):
        return f'Счёт {self.number} — {self.amount} ₽ ({self.get_status_display()})'

    def save(self, *args, **kwargs):
        if not self.number:
            from django.utils import timezone
            year = timezone.now().year
            last = Invoice.objects.filter(number__startswith=f'INV-{year}-').count()
            self.number = f'INV-{year}-{last + 1:04d}'
        super().save(*args, **kwargs)

    @property
    def is_paid(self):
        return self.status == self.STATUS_PAID


class Payment(models.Model):
    """Факт оплаты через TBank API."""
    STATUS_PENDING = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_REFUNDED = 'refunded'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_CONFIRMED, 'Подтверждён'),
        (STATUS_CANCELLED, 'Отменён'),
        (STATUS_REFUNDED, 'Возврат'),
    ]

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name='payments', verbose_name='Счёт'
    )
    tbank_payment_id = models.CharField('ID платежа TBank', max_length=200, unique=True)
    amount = models.DecimalField('Сумма (₽)', max_digits=12, decimal_places=2)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    raw_response = models.JSONField('Ответ TBank', default=dict)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    confirmed_at = models.DateTimeField('Подтверждён', null=True, blank=True)

    class Meta:
        verbose_name = 'Платёж'
        verbose_name_plural = 'Платежи'
        ordering = ['-created_at']

    def __str__(self):
        return f'Платёж {self.tbank_payment_id} — {self.amount} ₽'


class SubscriptionPurchase(models.Model):
    """Активная подписка пользователя на конкретный канал (299 ₽/мес за 1 паблик)."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='subscription_purchases', verbose_name='Пользователь'
    )
    channel = models.ForeignKey(
        'channels.Channel', on_delete=models.CASCADE,
        related_name='subscriptions', verbose_name='Канал'
    )
    payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='subscriptions', verbose_name='Платёж'
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, verbose_name='Тариф'
    )
    starts_at = models.DateTimeField('Начало')
    ends_at = models.DateTimeField('Окончание')
    is_active = models.BooleanField('Активна', default=True)
    auto_renew = models.BooleanField('Автопродление', default=True)
    created_at = models.DateTimeField('Создана', auto_now_add=True)

    class Meta:
        verbose_name = 'Подписка на канал'
        verbose_name_plural = 'Подписки на каналы'
        indexes = [
            models.Index(fields=['channel', 'is_active', 'ends_at']),
            models.Index(fields=['user', 'is_active']),
        ]

    def __str__(self):
        return f'{self.user} — {self.channel} до {self.ends_at:%d.%m.%Y}'

    @property
    def is_expired(self):
        return self.ends_at < timezone.now()

    def days_left(self):
        delta = self.ends_at - timezone.now()
        return max(0, delta.days)
