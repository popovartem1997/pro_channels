# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0010_advertiser_ord_model_scheme'),
    ]

    operations = [
        migrations.AddField(
            model_name='adapplication',
            name='owner_offered_payment_method',
            field=models.CharField(
                blank=True,
                choices=[('transfer', 'Перевод по реквизитам'), ('tbank', 'Онлайн (TBank)')],
                help_text='После одобрения рекламодатель видит только этот вариант.',
                max_length=20,
                verbose_name='Способ оплаты (предложен владельцем при одобрении)',
            ),
        ),
        migrations.AddField(
            model_name='adapplication',
            name='transfer_dest_bank_name',
            field=models.CharField(blank=True, max_length=255, verbose_name='Перевод: банк'),
        ),
        migrations.AddField(
            model_name='adapplication',
            name='transfer_dest_card_number',
            field=models.CharField(blank=True, max_length=64, verbose_name='Перевод: номер карты / телефона'),
        ),
        migrations.AddField(
            model_name='adapplication',
            name='transfer_dest_recipient_hint',
            field=models.CharField(
                blank=True,
                max_length=120,
                verbose_name='Перевод: получатель (имя и первая буква фамилии)',
            ),
        ),
        migrations.AddField(
            model_name='adapplication',
            name='transfer_screenshot',
            field=models.ImageField(
                blank=True,
                upload_to='ad_transfer_proofs/%Y/%m/',
                verbose_name='Скриншот перевода (рекламодатель)',
            ),
        ),
    ]
