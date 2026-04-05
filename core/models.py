from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.crypto import encrypt_token, decrypt_token


class GlobalApiKeys(models.Model):
    """
    Глобальные ключи сервиса (не из .env).

    Важно:
    - Ключи хранятся зашифрованно (Fernet, settings.BOTS_ENCRYPTION_KEY).
    - Код должен читать ключи ТОЛЬКО отсюда; если пусто — выдавать явную ошибку.
    """

    # DeepSeek (чат / рерайт; OpenAI-совместимый API)
    deepseek_api_key_enc = models.TextField(blank=True, verbose_name='DEEPSEEK_API_KEY (enc)')
    ai_rewrite_enabled = models.BooleanField(default=False, verbose_name='AI_REWRITE_ENABLED')

    # TBank
    tbank_terminal_key_enc = models.TextField(blank=True, verbose_name='TBANK_TERMINAL_KEY (enc)')
    tbank_secret_key_enc = models.TextField(blank=True, verbose_name='TBANK_SECRET_KEY (enc)')

    # VK ОРД (api.ord.vk.com, Bearer из кабинета ord.vk.com)
    vk_ord_access_token_enc = models.TextField(blank=True, verbose_name='VK_ORD_ACCESS_TOKEN (enc)')
    vk_ord_cabinet_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='VK ОРД: ID кабинета (справочно)',
        help_text='Не используется в REST API; для заметок.',
    )
    vk_ord_contract_external_id = models.CharField(
        max_length=220,
        blank=True,
        verbose_name='ОРД: внешний ID договора',
        help_text='Из кабинета ОРД VK — если креатив привязывается к договору (не самореклама).',
    )
    vk_ord_pad_external_id = models.CharField(
        max_length=220,
        blank=True,
        verbose_name='ОРД: внешний ID площадки по умолчанию',
        help_text='Для передачи статистики показов, если не задан у канала.',
    )
    vk_ord_operator_person_external_id = models.CharField(
        max_length=220,
        blank=True,
        verbose_name='ОРД: person исполнителя (оператор ProChannels)',
        help_text='Внешний id контрагента в ОРД для вашей организации (исполнитель по договору с рекламодателем). '
        'Нужен для автоматического создания договора; person рекламодателя создаётся без этого поля.',
    )
    vk_ord_use_sandbox = models.BooleanField(
        default=False,
        verbose_name='ОРД: песочница (sandbox)',
        help_text='Запросы на api-sandbox.ord.vk.com вместо боя.',
    )
    vk_ord_contract_sum_from_campaign_total = models.BooleanField(
        default=False,
        verbose_name='ОРД договор: сумма из стоимости заявки',
        help_text='Если включено — в договор в ОРД в поле amount подставляется итог текущей заявки (на шаге «ВК ОРД» в мастере). '
        'Если выключено — всегда используется фиксированная сумма ниже.',
    )
    vk_ord_contract_amount_fixed = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0'),
        verbose_name='ОРД договор: фиксированная сумма (₽)',
        help_text='Подставляется в amount при выключенной опции «сумма из заявки», а также когда итог заявки ещё не рассчитан (0 ₽) — регистрация, профиль.',
    )

    # Telegram parsing (Telethon user API)
    telegram_api_id = models.CharField(max_length=50, blank=True, verbose_name='TELEGRAM_API_ID')
    telegram_api_hash_enc = models.TextField(blank=True, verbose_name='TELEGRAM_API_HASH (enc)')

    # VK parsing
    vk_parse_access_token_enc = models.TextField(blank=True, verbose_name='VK_PARSE_ACCESS_TOKEN (enc)')

    parse_media_retention_days = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        verbose_name='Парсинг: хранить локальные медиа (дней)',
        help_text='Файлы в media/parsed_items/ старше этого срока удаляются (Celery раз в сутки); у старых записей парсинга поле «медиа» обнуляется.',
    )
    parse_media_disk_quota_bytes = models.PositiveBigIntegerField(
        default=5368709120,
        validators=[MaxValueValidator(1099511627776)],  # 1 TiB
        verbose_name='PARSE_MEDIA_DISK_QUOTA_BYTES',
        help_text='Суммарный лимит байт для media/parsed_items + media/imports/tg_to_max. 0 — квота отключена. post_media не входит.',
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Глобальные ключи API'
        verbose_name_plural = 'Глобальные ключи API'

    # ── helpers (decrypt) ────────────────────────────────────────────────────
    def get_deepseek_api_key(self) -> str:
        k = decrypt_token(self.deepseek_api_key_enc)
        if (k or '').strip():
            return k.strip()
        try:
            from django.conf import settings
            return (getattr(settings, 'DEEPSEEK_API_KEY', '') or '').strip()
        except Exception:
            return ''

    def get_tbank_terminal_key(self) -> str:
        return decrypt_token(self.tbank_terminal_key_enc)

    def get_tbank_secret_key(self) -> str:
        return decrypt_token(self.tbank_secret_key_enc)

    def get_vk_ord_access_token(self) -> str:
        return decrypt_token(self.vk_ord_access_token_enc)

    def get_telegram_api_hash(self) -> str:
        return decrypt_token(self.telegram_api_hash_enc)

    def get_vk_parse_access_token(self) -> str:
        return decrypt_token(self.vk_parse_access_token_enc)

    # ── helpers (encrypt) ────────────────────────────────────────────────────
    def set_deepseek_api_key(self, value: str):
        self.deepseek_api_key_enc = encrypt_token((value or '').strip())

    def set_tbank_terminal_key(self, value: str):
        self.tbank_terminal_key_enc = encrypt_token((value or '').strip())

    def set_tbank_secret_key(self, value: str):
        self.tbank_secret_key_enc = encrypt_token((value or '').strip())

    def set_telegram_api_hash(self, value: str):
        self.telegram_api_hash_enc = encrypt_token((value or '').strip())

    def set_vk_parse_access_token(self, value: str):
        self.vk_parse_access_token_enc = encrypt_token((value or '').strip())

    def set_vk_ord_access_token(self, value: str):
        # Пользователи часто вставляют строку целиком "Bearer xxx".
        v = (value or '').strip()
        if v.lower().startswith('bearer '):
            v = v.split(' ', 1)[1].strip()
        self.vk_ord_access_token_enc = encrypt_token(v)


def get_global_api_keys() -> GlobalApiKeys:
    """Singleton accessor (creates row if missing)."""
    obj, _ = GlobalApiKeys.objects.get_or_create(pk=1)
    return obj


def effective_parse_media_retention_days() -> int:
    """Дней хранения media/parsed_items: из GlobalApiKeys, иначе settings.PARSE_MEDIA_RETENTION_DAYS."""
    from django.conf import settings

    gk = get_global_api_keys()
    try:
        d = int(gk.parse_media_retention_days)
    except (TypeError, ValueError):
        d = 0
    if 1 <= d <= 365:
        return d
    return max(1, min(int(getattr(settings, 'PARSE_MEDIA_RETENTION_DAYS', 3) or 3), 365))


def effective_parse_media_disk_quota_bytes() -> int:
    """Лимит байт parsed_items + imports/tg_to_max: GlobalApiKeys; при некорректном значении — settings."""
    from django.conf import settings

    max_b = 1099511627776  # 1 TiB
    gk = get_global_api_keys()
    try:
        q = int(gk.parse_media_disk_quota_bytes)
    except (TypeError, ValueError):
        q = -1
    if q == 0:
        return 0
    if 1 <= q <= max_b:
        return q
    fb = int(getattr(settings, 'PARSE_MEDIA_DISK_QUOTA_BYTES', 0) or 0)
    if fb == 0:
        return 0
    return min(max(1, fb), max_b)


class PageVisit(models.Model):
    """Лог посещений страниц (аудит поведения пользователей)."""
    user = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='page_visits')
    method = models.CharField(max_length=10, blank=True)
    path = models.CharField(max_length=500, db_index=True)
    query_string = models.TextField(blank=True)
    referer = models.CharField(max_length=500, blank=True)
    user_agent = models.TextField(blank=True)
    ip = models.CharField(max_length=64, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Посещение страницы'
        verbose_name_plural = 'Посещения страниц'
        ordering = ['-created_at']

