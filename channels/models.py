"""
Channel — один паблик/канал в любой соцсети (TG, VK, MAX, Instagram).
Токены хранятся зашифрованными через bots.utils.
"""
import datetime

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
    CODE_PIN_24H = 'pin_24h'

    ADDON_KIND_CUSTOM = 'custom'
    ADDON_KIND_TOP_BLOCK = 'top_block'
    ADDON_KIND_PIN_HOURLY = 'pin_hourly'
    ADDON_KIND_CHOICES = [
        (ADDON_KIND_CUSTOM, 'Фиксированная цена (как раньше)'),
        (ADDON_KIND_TOP_BLOCK, 'Топ-блок: фикс. цена за N часов без других постов'),
        (ADDON_KIND_PIN_HOURLY, 'Закреп: цена за час × выбранные часы'),
    ]

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
    addon_kind = models.CharField(
        'Тип опции',
        max_length=20,
        choices=ADDON_KIND_CHOICES,
        default=ADDON_KIND_CUSTOM,
        help_text='Топ-блок: «Часов блока» = сколько часов без других постов (1 = топ на час), «Цена» = разово за услугу. '
        'Закреп почасовой: «Цена» за один час, «Макс. часов закрепа» ≥ 24 если нужен закреп на сутки; рекламодатель укажет часы (например 24).',
    )
    price = models.DecimalField(
        'Цена (₽)',
        max_digits=12,
        decimal_places=2,
        help_text='Для «Закреп почасовой» — цена за один час. Для топ-блока и custom — полная стоимость опции.',
    )
    block_hours = models.PositiveSmallIntegerField(
        'Часов блока (топ)',
        null=True,
        blank=True,
        help_text='Только для типа «Топ-блок»: 1, 2, 3… На это время после публикации не ставятся обычные посты.',
    )
    max_pin_hours = models.PositiveSmallIntegerField(
        'Макс. часов закрепа',
        default=72,
        help_text='Для «Закреп почасовой»: ограничение в мастере заявки.',
    )
    top_duration_minutes = models.PositiveIntegerField(
        'Длительность «топа» (мин., устар.)',
        default=0,
        help_text='Для старых опций с типом «Фиксированная»: если код начинается с top_, используется это значение. Для «Топ-блок» можно оставить 0 — берётся block_hours.',
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


class ChannelMorningDigest(models.Model):
    """
    Автоматическая публикация «утреннего дайджеста» в канал (погода, солнце, праздники,
    цитата / слово / гороскоп через DeepSeek при наличии ключа, картинка — отдельно от DeepSeek).
    """

    ZODIAC_GENERAL = 'general'
    ZODIAC_CHOICES = [
        (ZODIAC_GENERAL, 'Общий (без знака)'),
        ('aries', 'Овен'),
        ('taurus', 'Телец'),
        ('gemini', 'Близнецы'),
        ('cancer', 'Рак'),
        ('leo', 'Лев'),
        ('virgo', 'Дева'),
        ('libra', 'Весы'),
        ('scorpio', 'Скорпион'),
        ('sagittarius', 'Стрелец'),
        ('capricorn', 'Козерог'),
        ('aquarius', 'Водолей'),
        ('pisces', 'Рыбы'),
    ]

    channel = models.OneToOneField(
        Channel,
        on_delete=models.CASCADE,
        related_name='morning_digest',
        verbose_name='Канал',
    )
    is_enabled = models.BooleanField('Включено', default=False)

    send_time = models.TimeField(
        'Время отправки (локальное)',
        default=datetime.time(5, 0),
    )
    timezone_name = models.CharField(
        'Часовой пояс (IANA)',
        max_length=64,
        default='Europe/Moscow',
        help_text='Например: Europe/Moscow, Asia/Yekaterinburg',
    )
    weekdays = models.JSONField(
        'Дни недели (пусто = каждый день)',
        default=list,
        blank=True,
        help_text='Список чисел 0–6: пн=0 … вс=6. Пустой список — все дни.',
    )

    latitude = models.DecimalField('Широта', max_digits=9, decimal_places=6, default=55.7558)
    longitude = models.DecimalField('Долгота', max_digits=9, decimal_places=6, default=37.6173)
    location_label = models.CharField(
        'Название места (подпись)',
        max_length=120,
        blank=True,
        help_text='Например: Москва — показывается в шапке блока погоды.',
    )

    country_for_holidays = models.CharField(
        'Код страны для праздников (ISO)',
        max_length=2,
        default='RU',
        help_text='RU, UA, BY, KZ и др. — библиотека holidays.',
    )
    horoscope_sign = models.CharField(
        'Знак для гороскопа',
        max_length=20,
        choices=ZODIAC_CHOICES,
        default='general',
    )

    block_date = models.BooleanField('Дата', default=True)
    block_weather = models.BooleanField('Погода по периодам дня', default=True)
    block_sun = models.BooleanField('Восход и закат', default=True)
    block_quote = models.BooleanField('Цитата дня', default=True)
    block_english = models.BooleanField('Английское слово', default=True)
    block_holidays = models.BooleanField('Праздники сегодня', default=True)
    block_horoscope = models.BooleanField('Гороскоп на сегодня', default=True)
    block_image = models.BooleanField('Картинка к посту', default=True)

    use_ai_quote = models.BooleanField('Цитату генерировать через DeepSeek', default=True)
    use_ai_english = models.BooleanField('Слово дня — через DeepSeek', default=True)
    use_ai_horoscope = models.BooleanField('Гороскоп — через DeepSeek', default=True)

    image_seed_extra = models.CharField(
        'Доп. seed для картинки',
        max_length=80,
        blank=True,
        help_text='Уникальность картинки дня между каналами (любой короткий текст).',
    )

    last_sent_on = models.DateField('Последняя отправка (локальная дата)', null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Утренний дайджест канала'
        verbose_name_plural = 'Утренние дайджесты каналов'

    def __str__(self):
        return f'Дайджест · {self.channel.name}'


class ChannelInterestingFacts(models.Model):
    """
    Периодические «интересные факты» по заданной теме (DeepSeek) — посты только в черновики.
    """

    INTERVAL_6H = 6
    INTERVAL_12H = 12
    INTERVAL_24H = 24
    INTERVAL_48H = 48
    INTERVAL_72H = 72
    INTERVAL_168H = 168
    INTERVAL_CHOICES = [
        (INTERVAL_6H, 'Каждые 6 часов'),
        (INTERVAL_12H, 'Каждые 12 часов'),
        (INTERVAL_24H, 'Раз в сутки'),
        (INTERVAL_48H, 'Раз в 2 суток'),
        (INTERVAL_72H, 'Раз в 3 суток'),
        (INTERVAL_168H, 'Раз в неделю'),
    ]

    channel = models.OneToOneField(
        Channel,
        on_delete=models.CASCADE,
        related_name='interesting_facts',
        verbose_name='Канал',
    )
    is_enabled = models.BooleanField('Включено', default=False)
    topic = models.TextField(
        'Тема / запрос',
        blank=True,
        help_text='Например: интересные факты о городе Щёлково Московской области. Обязательно, если включена автогенерация.',
    )
    interval_hours = models.PositiveSmallIntegerField(
        'Периодичность',
        choices=INTERVAL_CHOICES,
        default=INTERVAL_24H,
    )
    last_generated_at = models.DateTimeField('Последняя генерация', null=True, blank=True)
    last_error = models.TextField('Последняя ошибка', blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Интересные факты (канал)'
        verbose_name_plural = 'Интересные факты (каналы)'

    def __str__(self):
        return f'Факты · {self.channel.name}'


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

    download_tg_media = models.BooleanField(
        'Скачивать медиа из Telegram',
        default=True,
        help_text='Выключите для ускорения: в MAX только текст; посты только с файлами без подписи пропускаются.',
    )

    celery_task_id = models.CharField(
        'ID задачи Celery',
        max_length=255,
        blank=True,
        default='',
        help_text='Идентификатор задачи в брокере (для диагностики очереди).',
    )

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
