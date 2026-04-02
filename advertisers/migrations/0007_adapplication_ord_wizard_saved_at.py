from django.db import migrations, models
from django.db.models import F


def copy_ord_synced_to_wizard(apps, schema_editor):
    AdApplication = apps.get_model('advertisers', 'AdApplication')
    AdApplication.objects.filter(ord_synced_at__isnull=False).update(
        ord_wizard_saved_at=F('ord_synced_at')
    )


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0006_adapplication_advertisingslot_act_link'),
    ]

    operations = [
        migrations.AddField(
            model_name='adapplication',
            name='ord_wizard_saved_at',
            field=models.DateTimeField(
                blank=True,
                help_text='Ставится при сохранении шага ОРД (в т.ч. с пустыми полями), чтобы продолжить мастер в правильном порядке.',
                null=True,
                verbose_name='Мастер: шаг ОРД сохранён',
            ),
        ),
        migrations.RunPython(copy_ord_synced_to_wizard, migrations.RunPython.noop),
    ]
