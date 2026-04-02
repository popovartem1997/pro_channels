from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parsing', '0010_parsekeyword_channel_group'),
    ]

    operations = [
        migrations.AlterField(
            model_name='airerewritejob',
            name='model_name',
            field=models.CharField(default='deepseek-chat', max_length=100, verbose_name='Модель AI'),
        ),
    ]
