from django.db import models

from core.crypto import encrypt_token, decrypt_token


class GlobalApiKeys(models.Model):
    """
    Глобальные ключи сервиса (не из .env).

    Важно:
    - Ключи хранятся зашифрованно (Fernet, settings.BOTS_ENCRYPTION_KEY).
    - Код должен читать ключи ТОЛЬКО отсюда; если пусто — выдавать явную ошибку.
    """

    # OpenAI
    openai_api_key_enc = models.TextField(blank=True, verbose_name='OPENAI_API_KEY (enc)')
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
    vk_ord_use_sandbox = models.BooleanField(
        default=False,
        verbose_name='ОРД: песочница (sandbox)',
        help_text='Запросы на api-sandbox.ord.vk.com вместо боя.',
    )

    # Telegram parsing (Telethon user API)
    telegram_api_id = models.CharField(max_length=50, blank=True, verbose_name='TELEGRAM_API_ID')
    telegram_api_hash_enc = models.TextField(blank=True, verbose_name='TELEGRAM_API_HASH (enc)')

    # VK parsing
    vk_parse_access_token_enc = models.TextField(blank=True, verbose_name='VK_PARSE_ACCESS_TOKEN (enc)')

    # Telegram import bot (Premium emoji helper)
    tg_import_bot_token_enc = models.TextField(blank=True, verbose_name='TG_IMPORT_BOT_TOKEN (enc)')

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Глобальные ключи API'
        verbose_name_plural = 'Глобальные ключи API'

    # ── helpers (decrypt) ────────────────────────────────────────────────────
    def get_openai_api_key(self) -> str:
        return decrypt_token(self.openai_api_key_enc)

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

    def get_tg_import_bot_token(self) -> str:
        return decrypt_token(self.tg_import_bot_token_enc)

    # ── helpers (encrypt) ────────────────────────────────────────────────────
    def set_openai_api_key(self, value: str):
        self.openai_api_key_enc = encrypt_token((value or '').strip())

    def set_tbank_terminal_key(self, value: str):
        self.tbank_terminal_key_enc = encrypt_token((value or '').strip())

    def set_tbank_secret_key(self, value: str):
        self.tbank_secret_key_enc = encrypt_token((value or '').strip())

    def set_vk_ord_access_token(self, value: str):
        self.vk_ord_access_token_enc = encrypt_token((value or '').strip())

    def set_telegram_api_hash(self, value: str):
        self.telegram_api_hash_enc = encrypt_token((value or '').strip())

    def set_vk_parse_access_token(self, value: str):
        self.vk_parse_access_token_enc = encrypt_token((value or '').strip())

    def set_tg_import_bot_token(self, value: str):
        self.tg_import_bot_token_enc = encrypt_token((value or '').strip())


def get_global_api_keys() -> GlobalApiKeys:
    """Singleton accessor (creates row if missing)."""
    obj, _ = GlobalApiKeys.objects.get_or_create(pk=1)
    return obj


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

