"""
Кастомная модель пользователя + тарифный план + подписка.

Тарифы:
  - free trial: 30 дней бесплатно (1 паблик)
  - basic: 299 руб/месяц за 1 паблик
"""
import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from datetime import timedelta


class User(AbstractUser):
    """Расширенный пользователь с данными для платформы."""

    phone = models.CharField(max_length=20, blank=True, verbose_name='Телефон')
    company = models.CharField(max_length=255, blank=True, verbose_name='Компания / ИП')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True, verbose_name='Аватар')

    ROLE_OWNER = 'owner'
    ROLE_ASSISTANT = 'assistant_admin'
    ROLE_MANAGER = 'manager'
    ROLE_ADVERTISER = 'advertiser'
    ROLE_CHOICES = [
        (ROLE_OWNER, 'Владелец'),
        (ROLE_ASSISTANT, 'Помощник-администратор'),
        (ROLE_MANAGER, 'Менеджер'),
        (ROLE_ADVERTISER, 'Рекламодатель'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_OWNER, verbose_name='Роль')
    is_email_verified = models.BooleanField(default=False, verbose_name='Email подтверждён')

    invited_by = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='invited_users',
        verbose_name='Приглашён пользователем'
    )
    trial_ends_at = models.DateTimeField(null=True, blank=True, verbose_name='Конец пробного периода')

    class Meta:
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'

    def __str__(self):
        return self.email or self.username

    def save(self, *args, **kwargs):
        if not self.pk and not self.trial_ends_at:
            self.trial_ends_at = timezone.now() + timedelta(days=30)
        super().save(*args, **kwargs)

    @property
    def is_on_trial(self) -> bool:
        return bool(self.trial_ends_at and self.trial_ends_at > timezone.now())

    @property
    def trial_days_left(self) -> int:
        if not self.is_on_trial:
            return 0
        return (self.trial_ends_at - timezone.now()).days

    @property
    def active_subscriptions(self):
        # Активные оплаченные подписки на каналы (299 ₽/мес за 1 паблик)
        return self.subscription_purchases.filter(is_active=True)

    @property
    def can_add_channel(self) -> bool:
        # Админы не ограничены подписками/триалом
        if self.is_superuser or self.is_staff:
            return True
        # Политика: в пробный период можно иметь 1 канал бесплатно.
        # После пробного — можно добавлять/держать каналы только если на них есть активная подписка.
        # (Тариф: 299 ₽/мес за 1 паблик.)
        from channels.models import Channel
        channels_count = Channel.objects.filter(owner=self).count()
        if self.is_on_trial:
            return channels_count < 1
        return self.active_subscriptions.count() >= channels_count


class EmailVerification(models.Model):
    """Токен для подтверждения email."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_verifications', verbose_name='Пользователь')
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Подтверждение email'
        verbose_name_plural = 'Подтверждения email'

    def is_expired(self):
        return (timezone.now() - self.created_at).total_seconds() > 86400  # 24 часа


class PasswordResetToken(models.Model):
    """Токен для сброса пароля."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_tokens', verbose_name='Пользователь')
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Сброс пароля'
        verbose_name_plural = 'Сброс паролей'

    def is_expired(self):
        return (timezone.now() - self.created_at).total_seconds() > 3600  # 1 час


class Subscription(models.Model):
    """Подписка пользователя — 299 руб/мес за 1 паблик."""

    PLAN_FREE = 'free'
    PLAN_BASIC = 'basic'
    PLAN_CHOICES = [
        (PLAN_FREE, 'Бесплатный (пробный)'),
        (PLAN_BASIC, 'Базовый — 299 ₽/мес'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subscriptions', verbose_name='Пользователь')
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default=PLAN_BASIC, verbose_name='Тариф')
    is_active = models.BooleanField(default=True, verbose_name='Активна')
    starts_at = models.DateTimeField(default=timezone.now, verbose_name='Начало')
    ends_at = models.DateTimeField(verbose_name='Конец')
    payment_id = models.CharField(max_length=100, blank=True, verbose_name='ID платежа')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Подписка'
        verbose_name_plural = 'Подписки'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} — {self.get_plan_display()} до {self.ends_at.strftime("%d.%m.%Y")}'

    @property
    def is_expired(self) -> bool:
        return self.ends_at < timezone.now()
