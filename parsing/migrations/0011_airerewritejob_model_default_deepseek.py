"""
Смена DEFAULT у поля model_name в БД (MySQL).

Раньше здесь был AlterField с опечаткой model_name='airerewritejob' вместо
'airewritejob' (класс AIRewriteJob) — из-за этого Django давал KeyError при migrate.
Эта миграция не трогает граф state через AlterField.
"""
from django.db import migrations


def _set_mysql_default_forward(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    schema_editor.execute(
        "ALTER TABLE parsing_airewritejob MODIFY COLUMN model_name "
        "VARCHAR(100) NOT NULL DEFAULT 'deepseek-chat'"
    )


def _set_mysql_default_backward(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    schema_editor.execute(
        "ALTER TABLE parsing_airewritejob MODIFY COLUMN model_name "
        "VARCHAR(100) NOT NULL DEFAULT 'gpt-4o-mini'"
    )


class Migration(migrations.Migration):

    dependencies = [
        ('parsing', '0010_parsekeyword_channel_group'),
    ]

    operations = [
        migrations.RunPython(_set_mysql_default_forward, _set_mysql_default_backward),
    ]
