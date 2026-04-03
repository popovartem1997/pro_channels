"""
Рекламодатели: профиль, заказ на рекламу, акт выполненных работ.
"""
from django.db import models
from django.conf import settings


class Advertiser(models.Model):
    """Профиль рекламодателя (юрлицо или ИП)."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='advertiser_profile', verbose_name='Пользователь'
    )
    company_name = models.CharField('Название компании / ИП', max_length=255)
    inn = models.CharField('ИНН', max_length=12)
    kpp = models.CharField('КПП', max_length=9, blank=True)
    ogrn = models.CharField('ОГРН / ОГРНИП', max_length=15, blank=True)
    legal_address = models.TextField('Юридический адрес')
    actual_address = models.TextField('Фактический адрес', blank=True)
    contact_person = models.CharField('Контактное лицо', max_length=255)
    contact_phone = models.CharField('Телефон', max_length=20, blank=True)
    bank_name = models.CharField('Банк', max_length=255, blank=True)
    bank_account = models.CharField('Расчётный счёт', max_length=20, blank=True)
    bank_bik = models.CharField('БИК', max_length=9, blank=True)
    bank_corr_account = models.CharField('Корр. счёт', max_length=20, blank=True)
    contract_signed = models.BooleanField('Договор подписан', default=False)
    contract_file = models.FileField('Файл договора', upload_to='contracts/', blank=True)
    contract_date = models.DateField('Дата договора', null=True, blank=True)
    contract_number = models.CharField('Номер договора', max_length=50, blank=True)
    ord_person_external_id = models.CharField(
        'Внешний ID в ОРД VK (контрагент)',
        max_length=220,
        blank=True,
        help_text='Из кабинета ord.vk.com — для креативов с привязкой к рекламодателю (person).',
    )
    ORD_MODEL_SCHEME_AUTO = ''
    ORD_MODEL_SCHEME_JURIDICAL = 'juridical'
    ORD_MODEL_SCHEME_IP = 'ip'
    ORD_MODEL_SCHEME_PHYSICAL = 'physical'
    ORD_MODEL_SCHEME_FOREIGN_JURIDICAL = 'foreign_juridical'
    ORD_MODEL_SCHEME_FOREIGN_PHYSICAL = 'foreign_physical'
    ORD_MODEL_SCHEME_CHOICES = [
        (ORD_MODEL_SCHEME_AUTO, 'Авто по ИНН (10 цифр — юрлицо, 12 — ИП)'),
        (ORD_MODEL_SCHEME_JURIDICAL, 'Юридическое лицо (ООО, АО и т.п.)'),
        (ORD_MODEL_SCHEME_IP, 'Индивидуальный предприниматель (ИП)'),
        (ORD_MODEL_SCHEME_PHYSICAL, 'Физическое лицо (РФ)'),
        (ORD_MODEL_SCHEME_FOREIGN_JURIDICAL, 'Иностранное юридическое лицо'),
        (ORD_MODEL_SCHEME_FOREIGN_PHYSICAL, 'Иностранное физическое лицо'),
    ]
    ord_model_scheme = models.CharField(
        'Тип контрагента для ВК ОРД',
        max_length=32,
        blank=True,
        choices=ORD_MODEL_SCHEME_CHOICES,
        default='',
        help_text='Передаётся в ОРД в juridical_details.type. Если «Авто» — по длине ИНН; при 12 цифрах уточните ИП или физлицо.',
    )
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'Рекламодатель'
        verbose_name_plural = 'Рекламодатели'

    def __str__(self):
        return f'{self.company_name} (ИНН: {self.inn})'


class OrdContract(models.Model):
    """Зеркало договоров из кабинета VK ОРД (для удобства выбора/сопоставления)."""
    external_id = models.CharField('Внешний ID договора (ОРД)', max_length=220, unique=True, db_index=True)
    type = models.CharField('Тип договора (ОРД)', max_length=60, blank=True)
    client_external_id = models.CharField('Клиент (person external_id)', max_length=220, blank=True)
    contractor_external_id = models.CharField('Исполнитель (person external_id)', max_length=220, blank=True)
    date = models.CharField('Дата договора', max_length=20, blank=True)
    serial = models.CharField('Номер/серия', max_length=120, blank=True)
    raw = models.JSONField('RAW из ОРД', default=dict)
    advertiser = models.ForeignKey(
        Advertiser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ord_contracts',
        verbose_name='Рекламодатель (сопоставленный)',
    )
    updated_at = models.DateTimeField('Обновлён', auto_now=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'ОРД договор'
        verbose_name_plural = 'ОРД договоры'
        ordering = ['-updated_at']

    def __str__(self):
        return self.external_id


class AdvertisingOrder(models.Model):
    """Заказ на размещение рекламы."""
    STATUS_DRAFT = 'draft'
    STATUS_SUBMITTED = 'submitted'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_ACTIVE = 'active'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_SUBMITTED, 'На рассмотрении'),
        (STATUS_APPROVED, 'Одобрен'),
        (STATUS_REJECTED, 'Отклонён'),
        (STATUS_ACTIVE, 'Выполняется'),
        (STATUS_COMPLETED, 'Завершён'),
        (STATUS_CANCELLED, 'Отменён'),
    ]

    advertiser = models.ForeignKey(
        Advertiser, on_delete=models.CASCADE,
        related_name='orders', verbose_name='Рекламодатель'
    )
    channels = models.ManyToManyField(
        'channels.Channel', verbose_name='Каналы размещения'
    )
    title = models.CharField('Заголовок заказа', max_length=255)
    description = models.TextField('Описание рекламы')
    budget = models.DecimalField('Бюджет (₽)', max_digits=12, decimal_places=2)
    start_date = models.DateField('Дата начала')
    end_date = models.DateField('Дата окончания')
    repeat_interval_days = models.PositiveIntegerField(
        'Повтор публикации (дней)',
        default=0,
        help_text='0 — один раз, иначе публиковать каждые N дней в период кампании'
    )
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    invoice = models.OneToOneField(
        'billing.Invoice', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='advertising_order', verbose_name='Счёт'
    )
    post = models.OneToOneField(
        'content.Post', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='advertising_order', verbose_name='Пост (автопубликация)'
    )
    rejection_reason = models.TextField('Причина отказа', blank=True)
    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='moderated_orders', verbose_name='Модератор'
    )
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлён', auto_now=True)

    class Meta:
        verbose_name = 'Рекламный заказ'
        verbose_name_plural = 'Рекламные заказы'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} — {self.advertiser.company_name}'


class AdApplication(models.Model):
    """
    Заявка рекламодателя на размещение (мастер: канал → слоты → контент → ОРД → оплата).
    Параллельна старому AdvertisingOrder; новый сценарий ведёт через эту модель.
    """

    STATUS_DRAFT = 'draft'
    STATUS_PENDING_OWNER = 'pending_owner'
    STATUS_APPROVED_FOR_PAYMENT = 'approved_for_payment'
    STATUS_AWAITING_PAYMENT = 'awaiting_payment'
    STATUS_PAID = 'paid'
    STATUS_SCHEDULED = 'scheduled'
    STATUS_PUBLISHED = 'published'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_PENDING_OWNER, 'На согласовании у владельца'),
        (STATUS_APPROVED_FOR_PAYMENT, 'Одобрено владельцем, ожидает оплаты'),
        (STATUS_AWAITING_PAYMENT, 'Ожидает оплаты'),
        (STATUS_PAID, 'Оплачена'),
        (STATUS_SCHEDULED, 'Запланирована'),
        (STATUS_PUBLISHED, 'Опубликована'),
        (STATUS_COMPLETED, 'Завершена'),
        (STATUS_CANCELLED, 'Отменена'),
    ]

    PAY_TRANSFER = 'transfer'
    PAY_PAYMENT_LINK = 'payment_link'
    PAYMENT_METHOD_CHOICES = [
        (PAY_TRANSFER, 'Перевод по реквизитам'),
        (PAY_PAYMENT_LINK, 'Оплата по ссылке'),
    ]

    advertiser = models.ForeignKey(
        Advertiser,
        on_delete=models.CASCADE,
        related_name='ad_applications',
        verbose_name='Рекламодатель',
    )
    channel = models.ForeignKey(
        'channels.Channel',
        on_delete=models.CASCADE,
        related_name='ad_applications',
        verbose_name='Канал',
    )
    post = models.OneToOneField(
        'content.Post',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ad_application',
        verbose_name='Пост (черновик / публикация)',
    )
    status = models.CharField('Статус', max_length=28, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)

    selected_slot_ids = models.JSONField('Выбранные слоты (id)', default=list, blank=True)
    addon_codes = models.JSONField('Доп. услуги (коды)', default=list, blank=True)
    ad_pin_hours = models.PositiveSmallIntegerField(
        'Закреп: выбрано часов (почасовая опция)',
        default=0,
        help_text='Если у канала включён закреп с ценой за час — сколько часов оплатил рекламодатель.',
    )

    price_subtotal = models.DecimalField('Сумма без скидки', max_digits=12, decimal_places=2, default=0)
    discount_percent = models.DecimalField('Скидка, %', max_digits=5, decimal_places=2, default=0)
    addons_total = models.DecimalField('Доп. услуги, ₽', max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField('Итого, ₽', max_digits=12, decimal_places=2, default=0)

    ord_contract_external_id = models.CharField('ОРД: договор', max_length=220, blank=True)
    ord_person_external_id = models.CharField('ОРД: person', max_length=220, blank=True)
    ord_pad_external_id = models.CharField('ОРД: pad', max_length=220, blank=True)
    ord_synced_at = models.DateTimeField('ОРД: синхронизировано', null=True, blank=True)
    ord_sync_error = models.TextField('ОРД: ошибка синхронизации', blank=True)
    ord_wizard_saved_at = models.DateTimeField(
        'Мастер: шаг ОРД сохранён',
        null=True,
        blank=True,
        help_text='Ставится при сохранении шага ОРД (в т.ч. с пустыми полями), чтобы продолжить мастер в правильном порядке.',
    )

    payment_method = models.CharField('Способ оплаты', max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True)
    owner_offered_payment_method = models.CharField(
        'Способ оплаты (предложен владельцем при одобрении)',
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        blank=True,
        help_text='После одобрения рекламодатель видит только этот вариант.',
    )
    transfer_dest_card_number = models.CharField(
        'Перевод: номер карты / телефона',
        max_length=64,
        blank=True,
    )
    transfer_dest_bank_name = models.CharField('Перевод: банк', max_length=255, blank=True)
    transfer_dest_recipient_hint = models.CharField(
        'Перевод: получатель (имя и первая буква фамилии)',
        max_length=120,
        blank=True,
    )
    owner_payment_url = models.TextField(
        'Ссылка на оплату (владелец вставляет вручную)',
        blank=True,
        help_text='Например ссылка из личного кабинета T-Bank; API эквайринга не вызывается.',
    )
    transfer_screenshot = models.ImageField(
        'Скриншот перевода (рекламодатель)',
        upload_to='ad_transfer_proofs/%Y/%m/',
        blank=True,
    )
    invoice = models.ForeignKey(
        'billing.Invoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        unique=True,
        related_name='ad_application',
        verbose_name='Счёт',
    )
    transfer_marked_received = models.BooleanField('Перевод подтверждён (вручную)', default=False)

    contract_signed_at = models.DateTimeField('Договор подписан (электронно)', null=True, blank=True)
    contract_sign_ip = models.GenericIPAddressField('IP подписи', null=True, blank=True)
    contract_body_html = models.TextField('Текст договора (снимок)', blank=True)

    owner_notes = models.TextField('Заметки владельца', blank=True)
    submitted_to_owner_at = models.DateTimeField('Отправлено владельцу', null=True, blank=True)
    owner_approved_at = models.DateTimeField('Одобрено владельцем', null=True, blank=True)
    owner_last_rejection_reason = models.TextField('Причина отказа владельца (последняя)', blank=True)

    created_at = models.DateTimeField('Создана', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлена', auto_now=True)

    class Meta:
        verbose_name = 'Заявка на рекламу (новый поток)'
        verbose_name_plural = 'Заявки на рекламу (новый поток)'
        ordering = ['-created_at']

    def __str__(self):
        return f'Заявка #{self.pk} · {self.advertiser.company_name} · {self.channel.name}'


class AdvertisingSlot(models.Model):
    """Слот времени в канале для рекламной публикации."""

    channel = models.ForeignKey(
        'channels.Channel',
        on_delete=models.CASCADE,
        related_name='advertising_slots',
        verbose_name='Канал',
    )
    starts_at = models.DateTimeField('Начало слота', db_index=True)
    application = models.ForeignKey(
        AdApplication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='booked_slots',
        verbose_name='Заявка',
    )

    class Meta:
        verbose_name = 'Слот рекламы'
        verbose_name_plural = 'Слоты рекламы'
        ordering = ['starts_at']
        constraints = [
            models.UniqueConstraint(fields=['channel', 'starts_at'], name='uniq_channel_ad_slot_starts_at'),
        ]

    def __str__(self):
        return f'{self.channel.name} @ {self.starts_at}'


class Act(models.Model):
    """Акт выполненных работ."""
    order = models.ForeignKey(
        AdvertisingOrder,
        on_delete=models.CASCADE,
        related_name='acts',
        verbose_name='Заказ',
        null=True,
        blank=True,
    )
    ad_application = models.ForeignKey(
        AdApplication,
        on_delete=models.CASCADE,
        related_name='acts',
        verbose_name='Заявка (новый поток)',
        null=True,
        blank=True,
    )
    number = models.CharField('Номер акта', max_length=50, unique=True)
    amount = models.DecimalField('Сумма (₽)', max_digits=12, decimal_places=2)
    service_description = models.TextField('Описание услуг')
    issued_at = models.DateField('Дата выдачи')
    signed_at = models.DateField('Дата подписания', null=True, blank=True)
    pdf_file = models.FileField('PDF акта', upload_to='acts/', blank=True)
    is_signed = models.BooleanField('Подписан', default=False)
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'Акт выполненных работ'
        verbose_name_plural = 'Акты выполненных работ'
        ordering = ['-issued_at']

    def __str__(self):
        return f'Акт {self.number} от {self.issued_at:%d.%m.%Y}'

    def save(self, *args, **kwargs):
        if not self.number:
            from django.utils import timezone
            year = timezone.now().year
            last = Act.objects.filter(number__startswith=f'ACT-{year}-').count()
            self.number = f'ACT-{year}-{last + 1:04d}'
        super().save(*args, **kwargs)
