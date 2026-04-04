"""
Управление командой: приглашение менеджеров, роли, права доступа.
"""
import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta


class TeamInvite(models.Model):
    """Приглашение менеджера по email."""
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_DECLINED = 'declined'
    STATUS_EXPIRED = 'expired'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_ACCEPTED, 'Принято'),
        (STATUS_DECLINED, 'Отклонено'),
        (STATUS_EXPIRED, 'Истекло'),
    ]

    ROLE_ASSISTANT = 'assistant_admin'
    ROLE_MANAGER = 'manager'
    ROLE_CHOICES = [
        (ROLE_ASSISTANT, 'Помощник-администратор'),
        (ROLE_MANAGER, 'Менеджер'),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='sent_invites', verbose_name='Владелец'
    )
    email = models.EmailField('Email приглашённого')
    role = models.CharField('Роль', max_length=20, choices=ROLE_CHOICES, default=ROLE_MANAGER)
    token = models.UUIDField('Токен', default=uuid.uuid4, unique=True)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    expires_at = models.DateTimeField('Истекает')
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accepted_invites', verbose_name='Принял'
    )

    class Meta:
        verbose_name = 'Приглашение'
        verbose_name_plural = 'Приглашения'
        ordering = ['-created_at']

    def __str__(self):
        return f'Приглашение для {self.email} ({self.get_role_display()})'

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=7)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return self.expires_at < timezone.now()


class TeamMember(models.Model):
    """Член команды владельца (помощник или менеджер)."""
    ROLE_ASSISTANT = 'assistant_admin'
    ROLE_MANAGER = 'manager'
    ROLE_CHOICES = [
        (ROLE_ASSISTANT, 'Помощник-администратор'),
        (ROLE_MANAGER, 'Менеджер'),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='team_members', verbose_name='Владелец'
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='memberships', verbose_name='Менеджер'
    )
    role = models.CharField('Роль', max_length=20, choices=ROLE_CHOICES, default=ROLE_MANAGER)
    channels = models.ManyToManyField(
        'channels.Channel', blank=True, verbose_name='Доступные каналы'
    )
    can_publish = models.BooleanField('Может публиковать', default=True)
    can_moderate = models.BooleanField('Может модерировать предложки', default=True)
    can_view_stats = models.BooleanField('Видит статистику', default=True)
    can_manage_bots = models.BooleanField('Управляет ботами', default=False)
    telegram_user_id = models.BigIntegerField('Telegram ID менеджера', null=True, blank=True)
    max_user_id = models.CharField('MAX ID менеджера', max_length=100, blank=True, default='')
    joined_at = models.DateTimeField('Вступил', auto_now_add=True)
    is_active = models.BooleanField('Активен', default=True)

    class Meta:
        verbose_name = 'Член команды'
        verbose_name_plural = 'Члены команды'
        unique_together = ('owner', 'member')

    def __str__(self):
        return f'{self.member} → {self.owner} ({self.get_role_display()})'


def sync_team_member_platform_ids_from_user(user):
    """
    Копирует Telegram / MAX ID из профиля пользователя во все строки TeamMember,
    где он указан как member. Так экран «Команда» и личный кабинет показывают одно и то же.
    """
    TeamMember.objects.filter(member_id=user.pk).update(
        telegram_user_id=user.telegram_user_id,
        max_user_id=user.max_user_id or '',
    )
