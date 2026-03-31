from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0005_alter_tg_chat_id_verbose_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='channel',
            name='admin_contact_site',
            field=models.CharField(
                blank=True,
                help_text='Как показывать владельца в боте. Можно оставить пустым — будет взят username пользователя.',
                max_length=100,
                verbose_name='Ник админа (сайт)',
            ),
        ),
        migrations.AddField(
            model_name='channel',
            name='admin_contact_tg',
            field=models.CharField(
                blank=True,
                help_text='Например: @myadmin (или username без @).',
                max_length=100,
                verbose_name='Ник админа (Telegram)',
            ),
        ),
        migrations.AddField(
            model_name='channel',
            name='admin_contact_vk',
            field=models.CharField(
                blank=True,
                help_text='Например: https://vk.com/id123 или https://vk.com/username',
                max_length=100,
                verbose_name='Ник/ссылка админа (VK)',
            ),
        ),
        migrations.AddField(
            model_name='channel',
            name='admin_contact_max_phone',
            field=models.CharField(
                blank=True,
                help_text='Например: +79990000000',
                max_length=50,
                verbose_name='Телефон админа (MAX)',
            ),
        ),
    ]

