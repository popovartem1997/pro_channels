import decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_globalapikeys_vk_ord_operator_person'),
    ]

    operations = [
        migrations.AddField(
            model_name='globalapikeys',
            name='vk_ord_contract_sum_from_campaign_total',
            field=models.BooleanField(
                default=False,
                help_text='Если включено — в договор в ОРД в поле amount подставляется итог текущей заявки (на шаге «ВК ОРД» в мастере). '
                'Если выключено — всегда используется фиксированная сумма ниже.',
                verbose_name='ОРД договор: сумма из стоимости заявки',
            ),
        ),
        migrations.AddField(
            model_name='globalapikeys',
            name='vk_ord_contract_amount_fixed',
            field=models.DecimalField(
                decimal_places=2,
                default=decimal.Decimal('0'),
                help_text='Подставляется в amount при выключенной опции «сумма из заявки», а также когда итог заявки ещё не рассчитан (0 ₽) — регистрация, профиль.',
                max_digits=14,
                verbose_name='ОРД договор: фиксированная сумма (₽)',
            ),
        ),
    ]
