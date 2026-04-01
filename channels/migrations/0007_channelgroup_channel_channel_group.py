# Generated manually

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('channels', '0006_channel_admin_contacts'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChannelGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Название группы')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='channel_groups', to=settings.AUTH_USER_MODEL, verbose_name='Владелец')),
            ],
            options={
                'verbose_name': 'Группа каналов',
                'verbose_name_plural': 'Группы каналов',
                'ordering': ['name', 'pk'],
            },
        ),
        migrations.AddField(
            model_name='channel',
            name='channel_group',
            field=models.ForeignKey(
                blank=True,
                help_text='Один паблик в разных соцсетях: объедините TG, VK, MAX в одну группу для фильтра в парсинге.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='channels',
                to='channels.channelgroup',
                verbose_name='Группа каналов',
            ),
        ),
    ]
