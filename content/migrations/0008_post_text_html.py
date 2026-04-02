from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0007_postmedia_order_default_one'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='text_html',
            field=models.TextField(blank=True, verbose_name='Текст поста (HTML для Telegram)'),
        ),
    ]

