from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0009_adapplication_ad_pin_hours'),
    ]

    operations = [
        migrations.AddField(
            model_name='advertiser',
            name='ord_model_scheme',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', 'Авто по ИНН (10 цифр — юрлицо, 12 — ИП)'),
                    ('juridical', 'Юридическое лицо (ООО, АО и т.п.)'),
                    ('ip', 'Индивидуальный предприниматель (ИП)'),
                    ('physical', 'Физическое лицо (РФ)'),
                    ('foreign_juridical', 'Иностранное юридическое лицо'),
                    ('foreign_physical', 'Иностранное физическое лицо'),
                ],
                default='',
                help_text='Передаётся в ОРД в juridical_details.type. Если «Авто» — по длине ИНН; при 12 цифрах уточните ИП или физлицо.',
                max_length=32,
                verbose_name='Тип контрагента для ВК ОРД',
            ),
        ),
    ]
