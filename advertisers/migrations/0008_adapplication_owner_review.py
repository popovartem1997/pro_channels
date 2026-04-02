from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0007_adapplication_ord_wizard_saved_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='adapplication',
            name='owner_approved_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Одобрено владельцем'),
        ),
        migrations.AddField(
            model_name='adapplication',
            name='owner_last_rejection_reason',
            field=models.TextField(blank=True, verbose_name='Причина отказа владельца (последняя)'),
        ),
        migrations.AddField(
            model_name='adapplication',
            name='submitted_to_owner_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Отправлено владельцу'),
        ),
        migrations.AlterField(
            model_name='adapplication',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Черновик'),
                    ('pending_owner', 'На согласовании у владельца'),
                    ('approved_for_payment', 'Одобрено владельцем, ожидает оплаты'),
                    ('awaiting_payment', 'Ожидает оплаты'),
                    ('paid', 'Оплачена'),
                    ('scheduled', 'Запланирована'),
                    ('published', 'Опубликована'),
                    ('completed', 'Завершена'),
                    ('cancelled', 'Отменена'),
                ],
                db_index=True,
                default='draft',
                max_length=28,
                verbose_name='Статус',
            ),
        ),
    ]
