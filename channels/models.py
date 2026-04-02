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

    # Реклама: слоты и сроки (кабинет рекламодателя)
    ad_slot_schedule_json = models.JSONField(
        'Расписание слотов (JSON)',
        default=list,
        blank=True,
        help_text='Список объектов: {"weekday": 0-6 (пн=0), "times": ["10:00", "14:00", ...]}. '
        'Из расписания генерируются свободные слоты.',
    )
    ad_slot_horizon_days = models.PositiveIntegerField(
        'На сколько дней вперёд слоты',
        default=56,
        help_text='Генерация свободных дат для выбора в заявке.',
    )
    ad_post_lifetime_days = models.PositiveIntegerField(
        'Срок «рекламного» поста (дней)',
        default=7,
        help_text='После публикации — период для акта и учёта; задаётся владельцем канала.',
    )
    ad_publish_pause_until = models.DateTimeField(
        'Пауза публикаций до',
        null=True,
        blank=True,
        help_text='Пока время не прошло, очередные посты в этот канал не публикуются (например, оплаченный «топ»).',
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


class ChannelAdVolumeDiscount(models.Model):
    """Скидка от количества постов в одной заявке (ступени: от N постов — процент)."""

    channel = models.ForeignKey(
        Channel,
        on_delete=models.CASCADE,
        related_name='ad_volume_discounts',
        verbose_name='Канал',
    )
    min_posts = models.PositiveIntegerField(
        'От количества постов',
        help_text='Если в заявке выбрано не меньше слотов — применяется эта скидка (берётся наибольшая подходящая ступень).',
    )
    discount_percent = models.DecimalField(
        'Скидка, %',
        max_digits=5,
        decimal_places=2,
        help_text='Например 10.00 = 10%.',
    )

    class Meta:
        verbose_name = 'Скидка за объём (реклама)'
        verbose_name_plural = 'Скидки за объём (реклама)'
        ordering = ['channel_id', 'min_posts']
        constraints = [
            models.UniqueConstraint(fields=['channel', 'min_posts'], name='uniq_channel_ad_vol_discount_min_posts'),
        ]

    def __str__(self):
        return f'{self.channel_id}: от {self.min_posts} постов — {self.discount_percent}%'


class ChannelAdAddon(models.Model):
    """Доп. услуги к размещению: закреп, топ N часов и т.д."""

    CODE_PIN = 'pin'
    CODE_TOP_1H = 'top_1h'
    CODE_TOP_2H = 'top_2h'

    channel = models.ForeignKey(
        Channel,
        on_delete=models.CASCADE,
        related_name='ad_addons',
        verbose_name='Канал',
    )
    code = models.CharField(
        'Код',
        max_length=32,
        help_text='Рекомендуется: pin, top_1h, top_2h — публикация учитывает top_* для паузы очереди.',
    )
    title = models.CharField('Название для рекламодателя', max_length=120)
    price = models.DecimalField('Цена (₽)', max_digits=12, decimal_places=2)
    top_duration_minutes = models.PositiveIntegerField(
        'Длительность «топа» (мин.)',
        default=0,
        help_text='Для закрепа — 0. Для топа — 60 или 120 и т.д.; после публикации на это время блокируются прочие посты в канал.',
    )
    is_active = models.BooleanField('Включено', default=True)

    class Meta:
        verbose_name = 'Доп. услуга (реклама)'
        verbose_name_plural = 'Доп. услуги (реклама)'
        constraints = [
            models.UniqueConstraint(fields=['channel', 'code'], name='uniq_channel_ad_addon_code'),
        ]

    def __str__(self):
        return f'{self.channel_id} · {self.title}'


class HistoryImportRun(models.Model):
    """
    Фоновый импорт истории сообщений из Telegram-канала в MAX-канал.
    Прогресс хранится в JSON, чтобы UI мог поллить статус даже после обновления страницы.
    """

    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_DONE = 'done'
    STATUS_ERROR = 'error'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'В очереди'),
        (STATUS_RUNNING, 'В работе'),
        (STATUS_DONE, 'Готово'),
        (STATUS_ERROR, 'Ошибка'),
        (STATUS_CANCELLED, 'Остановлено'),
    ]

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='history_import_runs',
        verbose_name='Запустил',
    )
    source_channel = models.ForeignKey(
        Channel,
        on_delete=models.CASCADE,
        related_name='history_import_runs_as_source',
        verbose_name='Источник (Telegram)',
    )
    target_channel = models.ForeignKey(
        Channel,
        on_delete=models.CASCADE,
        related_name='history_import_runs_as_target',
        verbose_name='Цель (MAX)',
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    progress_json = models.JSONField(default=dict)
    error_message = models.TextField(blank=True)

    cancel_requested = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Импорт истории TG→MAX'
        verbose_name_plural = 'Импорты истории TG→MAX'
        ordering = ['-created_at']
        indexes = [
            # Имя совпадает с 0009_historyimportrun — иначе makemigrations видит «лишний» индекс.
            models.Index(
                fields=['source_channel', 'target_channel', 'status'],
                name='channels_hi_source__b7b41a_idx',
            ),
        ]

    def __str__(self):
        return f'Import #{self.pk}: {self.source_channel_id} → {self.target_channel_id} ({self.status})'
