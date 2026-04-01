"""
Channel — один паблик/канал в любой соцсети (TG, VK, MAX, Instagram).
Токены хранятся зашифрованными через bots.utils.
"""
from django.db import models
from django.contrib.auth import get_user_model
from bots.utils import encrypt_token, decrypt_token

User = get_user_model()


class ChannelGroup(models.Model):
    """
    Объединяет несколько записей Channel (разные соцсети) в один логический паблик.
    Для парсинга и фильтров используется группа, а не отдельный канал.
    """
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='channel_groups', verbose_name='Владелец'
    )
    name = models.CharField(max_length=255, verbose_name='Название группы')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Группа каналов'
        verbose_name_plural = 'Группы каналов'
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name


class Channel(models.Model):
    PLATFORM_TELEGRAM = 'telegram'
    PLATFORM_VK = 'vk'
    PLATFORM_MAX = 'max'
    PLATFORM_INSTAGRAM = 'instagram'
    PLATFORM_CHOICES = [
        (PLATFORM_TELEGRAM, 'Telegram'),
        (PLATFORM_VK, 'ВКонтакте'),
        (PLATFORM_MAX, 'MAX'),
        (PLATFORM_INSTAGRAM, 'Instagram'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='channels', verbose_name='Владелец')
    channel_group = models.ForeignKey(
        ChannelGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='channels',
        verbose_name='Группа каналов',
        help_text='Один паблик в разных соцсетях: объедините TG, VK, MAX в одну группу для фильтра в парсинге.',
    )
    name = models.CharField(max_length=255, verbose_name='Название канала')
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, verbose_name='Платформа')
    description = models.TextField(blank=True, verbose_name='Описание')
    avatar = models.ImageField(upload_to='channel_avatars/', blank=True, null=True, verbose_name='Аватар')
    is_active = models.BooleanField(default=True, verbose_name='Активен')

    tg_chat_id = models.CharField(max_length=100, blank=True, verbose_name='Telegram ID канала')
    tg_bot_token_enc = models.TextField(blank=True, verbose_name='TG токен бота (зашифр.)')

    vk_group_id = models.CharField(max_length=50, blank=True, verbose_name='VK Group ID')
    vk_access_token_enc = models.TextField(blank=True, verbose_name='VK Access Token (зашифр.)')

    max_channel_id = models.CharField(max_length=100, blank=True, verbose_name='MAX Channel ID')
    max_bot_token_enc = models.TextField(blank=True, verbose_name='MAX токен бота (зашифр.)')

    ig_account_id = models.CharField(max_length=100, blank=True, verbose_name='Instagram Account ID')
    ig_access_token_enc = models.TextField(blank=True, verbose_name='Instagram Access Token (зашифр.)')

    tg_footer = models.TextField(
        blank=True,
        verbose_name='Подпись Telegram',
        help_text='HTML-разметка: <b>жирный</b>, <i>курсив</i>, <a href="url">ссылка</a>, <blockquote>цитата</blockquote>'
    )
    max_footer = models.TextField(
        blank=True,
        verbose_name='Подпись MAX',
        help_text='HTML-разметка: <b>жирный</b>, <i>курсив</i>, <a href="url">ссылка</a>'
    )
    vk_footer = models.TextField(
        blank=True,
        verbose_name='Подпись ВКонтакте',
        help_text='Обычный текст. Ссылки: [club123|Название] или https://...'
    )

    # Контакты владельца (для кнопки "Связаться с админом" в предложке)
    admin_contact_site = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Ник админа (сайт)',
        help_text='Как показывать владельца в боте. Можно оставить пустым — будет взят username пользователя.'
    )
    admin_contact_tg = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Ник админа (Telegram)',
        help_text='Например: @myadmin (или username без @).'
    )
    admin_contact_vk = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Ник/ссылка админа (VK)',
        help_text='Например: https://vk.com/id123 или https://vk.com/username'
    )
    admin_contact_max_phone = models.CharField(
        max_length=50,
        blank=True,
        verbose_name='Телефон админа (MAX)',
        help_text='Например: +79990000000'
    )

    subscribers_count = models.PositiveIntegerField(default=0, verbose_name='Подписчиков')
    last_synced_at = models.DateTimeField(null=True, blank=True, verbose_name='Последняя синхронизация')

    # Монетизация: витрина рекламы
    ad_enabled = models.BooleanField('Принимает рекламу', default=False)
    ad_price = models.DecimalField(
        'Цена за 1 размещение (₽)',
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Используется в кабинете рекламодателя для расчёта бюджета'
    )
    ord_pad_external_id = models.CharField(
        'ОРД VK: внешний ID площадки',
        max_length=220,
        blank=True,
        help_text='Площадка в кабинете ОРД для передачи статистики по этому каналу.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Канал'
        verbose_name_plural = 'Каналы'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.get_platform_display()})'

    def set_tg_token(self, token: str):
        self.tg_bot_token_enc = encrypt_token(token) if token else ''

    def get_tg_token(self) -> str:
        return decrypt_token(self.tg_bot_token_enc) if self.tg_bot_token_enc else ''

    def set_vk_token(self, token: str):
        self.vk_access_token_enc = encrypt_token(token) if token else ''

    def get_vk_token(self) -> str:
        return decrypt_token(self.vk_access_token_enc) if self.vk_access_token_enc else ''

    def set_max_token(self, token: str):
        self.max_bot_token_enc = encrypt_token(token) if token else ''

    def get_max_token(self) -> str:
        return decrypt_token(self.max_bot_token_enc) if self.max_bot_token_enc else ''

    def set_ig_token(self, token: str):
        self.ig_access_token_enc = encrypt_token(token) if token else ''

    def get_ig_token(self) -> str:
        return decrypt_token(self.ig_access_token_enc) if self.ig_access_token_enc else ''

    @property
    def platform_icon(self) -> str:
        icons = {'telegram': 'bi-telegram', 'vk': 'bi-people-fill', 'max': 'bi-broadcast', 'instagram': 'bi-instagram'}
        return icons.get(self.platform, 'bi-globe')

    @property
    def token_configured(self) -> bool:
        if self.platform == self.PLATFORM_TELEGRAM:
            return bool(self.tg_bot_token_enc and self.tg_chat_id)
        if self.platform == self.PLATFORM_VK:
            return bool(self.vk_access_token_enc and self.vk_group_id)
        if self.platform == self.PLATFORM_MAX:
            return bool(self.max_bot_token_enc and self.max_channel_id)
        if self.platform == self.PLATFORM_INSTAGRAM:
            return bool(self.ig_access_token_enc and self.ig_account_id)
        return False
