import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0005_ordcontract_model'),
        ('billing', '0001_initial'),
        ('channels', '0010_channel_ad_wizard_and_addons'),
        ('content', '0010_post_ad_top_block'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdApplication',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('draft', 'Черновик'), ('awaiting_payment', 'Ожидает оплаты'), ('paid', 'Оплачена'), ('scheduled', 'Запланирована'), ('published', 'Опубликована'), ('completed', 'Завершена'), ('cancelled', 'Отменена')], db_index=True, default='draft', max_length=24, verbose_name='Статус')),
                ('selected_slot_ids', models.JSONField(blank=True, default=list, verbose_name='Выбранные слоты (id)')),
                ('addon_codes', models.JSONField(blank=True, default=list, verbose_name='Доп. услуги (коды)')),
                ('price_subtotal', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='Сумма без скидки')),
                ('discount_percent', models.DecimalField(decimal_places=2, default=0, max_digits=5, verbose_name='Скидка, %')),
                ('addons_total', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='Доп. услуги, ₽')),
                ('total_amount', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='Итого, ₽')),
                ('ord_contract_external_id', models.CharField(blank=True, max_length=220, verbose_name='ОРД: договор')),
                ('ord_person_external_id', models.CharField(blank=True, max_length=220, verbose_name='ОРД: person')),
                ('ord_pad_external_id', models.CharField(blank=True, max_length=220, verbose_name='ОРД: pad')),
                ('ord_synced_at', models.DateTimeField(blank=True, null=True, verbose_name='ОРД: синхронизировано')),
                ('ord_sync_error', models.TextField(blank=True, verbose_name='ОРД: ошибка синхронизации')),
                ('payment_method', models.CharField(blank=True, choices=[('transfer', 'Перевод по реквизитам'), ('tbank', 'Онлайн (TBank)')], max_length=20, verbose_name='Способ оплаты')),
                ('transfer_marked_received', models.BooleanField(default=False, verbose_name='Перевод подтверждён (вручную)')),
                ('contract_signed_at', models.DateTimeField(blank=True, null=True, verbose_name='Договор подписан (электронно)')),
                ('contract_sign_ip', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP подписи')),
                ('contract_body_html', models.TextField(blank=True, verbose_name='Текст договора (снимок)')),
                ('owner_notes', models.TextField(blank=True, verbose_name='Заметки владельца')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создана')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлена')),
                ('advertiser', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ad_applications', to='advertisers.advertiser', verbose_name='Рекламодатель')),
                ('channel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ad_applications', to='channels.channel', verbose_name='Канал')),
                ('invoice', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ad_application', to='billing.invoice', unique=True, verbose_name='Счёт')),
                ('post', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ad_application', to='content.post', verbose_name='Пост (черновик / публикация)')),
            ],
            options={
                'verbose_name': 'Заявка на рекламу (новый поток)',
                'verbose_name_plural': 'Заявки на рекламу (новый поток)',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='AdvertisingSlot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('starts_at', models.DateTimeField(db_index=True, verbose_name='Начало слота')),
                ('application', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='booked_slots', to='advertisers.adapplication', verbose_name='Заявка')),
                ('channel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='advertising_slots', to='channels.channel', verbose_name='Канал')),
            ],
            options={
                'verbose_name': 'Слот рекламы',
                'verbose_name_plural': 'Слоты рекламы',
                'ordering': ['starts_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='advertisingslot',
            constraint=models.UniqueConstraint(fields=('channel', 'starts_at'), name='uniq_channel_ad_slot_starts_at'),
        ),
        migrations.AddField(
            model_name='act',
            name='ad_application',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='acts', to='advertisers.adapplication', verbose_name='Заявка (новый поток)'),
        ),
        migrations.AlterField(
            model_name='act',
            name='order',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='acts', to='advertisers.advertisingorder', verbose_name='Заказ'),
        ),
    ]
