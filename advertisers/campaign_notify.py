"""Ссылка на карточку заявки для владельца канала (уведомления в Telegram)."""
from django.conf import settings
from django.urls import reverse


def ad_owner_application_url(app_pk: int) -> str:
    base = (getattr(settings, 'SITE_URL', '') or '').strip().rstrip('/')
    path = reverse('advertisers:owner_campaign_detail', args=[app_pk])
    return f'{base}{path}' if base else ''
