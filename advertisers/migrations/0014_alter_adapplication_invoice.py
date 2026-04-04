# ForeignKey(unique=True) → OneToOneField (как в system check W342).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0001_initial'),
        ('advertisers', '0013_alter_advertiser_company_name'),
    ]

    operations = [
        migrations.AlterField(
            model_name='adapplication',
            name='invoice',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='ad_application',
                to='billing.invoice',
                verbose_name='Счёт',
            ),
        ),
    ]
