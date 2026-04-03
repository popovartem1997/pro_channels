# Generated manually

from django.db import migrations, models


def forwards_tbank_to_payment_link(apps, schema_editor):
    AdApplication = apps.get_model('advertisers', 'AdApplication')
    AdApplication.objects.filter(owner_offered_payment_method='tbank').update(owner_offered_payment_method='payment_link')
    AdApplication.objects.filter(payment_method='tbank').update(payment_method='payment_link')


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0011_adapplication_owner_payment_offer'),
    ]

    operations = [
        migrations.AddField(
            model_name='adapplication',
            name='owner_payment_url',
            field=models.TextField(
                blank=True,
                help_text='Например ссылка из личного кабинета T-Bank; API эквайринга не вызывается.',
                verbose_name='Ссылка на оплату (владелец вставляет вручную)',
            ),
        ),
        migrations.AlterField(
            model_name='adapplication',
            name='owner_offered_payment_method',
            field=models.CharField(
                blank=True,
                choices=[
                    ('transfer', 'Перевод по реквизитам'),
                    ('payment_link', 'Оплата по ссылке'),
                ],
                help_text='После одобрения рекламодатель видит только этот вариант.',
                max_length=20,
                verbose_name='Способ оплаты (предложен владельцем при одобрении)',
            ),
        ),
        migrations.AlterField(
            model_name='adapplication',
            name='payment_method',
            field=models.CharField(
                blank=True,
                choices=[
                    ('transfer', 'Перевод по реквизитам'),
                    ('payment_link', 'Оплата по ссылке'),
                ],
                max_length=20,
                verbose_name='Способ оплаты',
            ),
        ),
        migrations.RunPython(forwards_tbank_to_payment_link, migrations.RunPython.noop),
    ]
