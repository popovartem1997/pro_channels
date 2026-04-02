from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_user_is_email_verified_alter_user_role_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='ad_payment_phone',
            field=models.CharField(
                blank=True,
                help_text='Номер для СБП / перевода по номеру телефона в шаге оплаты заявки.',
                max_length=40,
                verbose_name='Телефон для перевода (реклама)',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='ad_payment_instructions',
            field=models.TextField(
                blank=True,
                help_text='Доп. данные: банк, ФИО получателя, комментарий к переводу.',
                verbose_name='Текст реквизитов для перевода (реклама)',
            ),
        ),
    ]
