from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_pagevisit'),
    ]

    operations = [
        migrations.AddField(
            model_name='globalapikeys',
            name='vk_ord_contract_external_id',
            field=models.CharField(
                blank=True,
                help_text='Из кабинета ОРД VK — если креатив привязывается к договору (не самореклама).',
                max_length=220,
                verbose_name='ОРД: внешний ID договора',
            ),
        ),
        migrations.AddField(
            model_name='globalapikeys',
            name='vk_ord_pad_external_id',
            field=models.CharField(
                blank=True,
                help_text='Для передачи статистики показов, если не задан у канала.',
                max_length=220,
                verbose_name='ОРД: внешний ID площадки по умолчанию',
            ),
        ),
        migrations.AddField(
            model_name='globalapikeys',
            name='vk_ord_use_sandbox',
            field=models.BooleanField(
                default=False,
                help_text='Запросы на api-sandbox.ord.vk.com вместо боя.',
                verbose_name='ОРД: песочница (sandbox)',
            ),
        ),
        migrations.AlterField(
            model_name='globalapikeys',
            name='vk_ord_cabinet_id',
            field=models.CharField(
                blank=True,
                help_text='Не используется в REST API; для заметок.',
                max_length=100,
                verbose_name='VK ОРД: ID кабинета (справочно)',
            ),
        ),
    ]
