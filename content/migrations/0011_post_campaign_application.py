import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('advertisers', '0006_adapplication_advertisingslot_act_link'),
        ('content', '0010_post_ad_top_block'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='campaign_application',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='campaign_posts',
                to='advertisers.adapplication',
                verbose_name='Заявка на рекламу (публикации)',
            ),
        ),
    ]
