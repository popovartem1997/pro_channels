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
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'Рекламодатель'
        verbose_name_plural = 'Рекламодатели'

    def __str__(self):
        return f'{self.company_name} (ИНН: {self.inn})'


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


class Act(models.Model):
    """Акт выполненных работ."""
    order = models.ForeignKey(
        AdvertisingOrder, on_delete=models.CASCADE,
        related_name='acts', verbose_name='Заказ'
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
