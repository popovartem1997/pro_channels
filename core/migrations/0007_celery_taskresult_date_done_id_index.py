"""
Индекс для django_celery_results.TaskResult: ORDER BY date_done в мониторинге Celery.

Таблица создаётся приложением django_celery_results; миграция только добавляет индекс,
если таблица уже есть (после migrate django_celery_results).
"""

from django.db import migrations


IDX = 'pch_taskresult_date_done_id'
TABLE = 'django_celery_results_taskresult'


def _table_exists(cursor, vendor: str) -> bool:
    if vendor == 'mysql':
        cursor.execute(
            'SELECT 1 FROM information_schema.tables '
            'WHERE table_schema = DATABASE() AND table_name = %s LIMIT 1',
            [TABLE],
        )
        return cursor.fetchone() is not None
    if vendor == 'sqlite':
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s LIMIT 1",
            [TABLE],
        )
        return cursor.fetchone() is not None
    if vendor == 'postgresql':
        cursor.execute(
            'SELECT 1 FROM information_schema.tables '
            'WHERE table_schema = current_schema() AND table_name = %s LIMIT 1',
            [TABLE],
        )
        return cursor.fetchone() is not None
    return False


def _index_exists_mysql(cursor) -> bool:
    cursor.execute(
        'SELECT COUNT(*) FROM information_schema.statistics '
        'WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s',
        [TABLE, IDX],
    )
    row = cursor.fetchone()
    return bool(row and row[0] > 0)


def forwards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if not _table_exists(cursor, vendor):
            return
        if vendor == 'mysql':
            if _index_exists_mysql(cursor):
                return
            cursor.execute(
                f'CREATE INDEX `{IDX}` ON `{TABLE}` (`date_done`, `id`)'
            )
        elif vendor == 'sqlite':
            cursor.execute(
                f'CREATE INDEX IF NOT EXISTS "{IDX}" ON "{TABLE}" ("date_done", "id")'
            )
        elif vendor == 'postgresql':
            cursor.execute(
                f'CREATE INDEX IF NOT EXISTS "{IDX}" ON "{TABLE}" ("date_done" DESC NULLS LAST, "id" DESC)'
            )


def backwards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if not _table_exists(cursor, vendor):
            return
        if vendor == 'mysql':
            cursor.execute(f'DROP INDEX `{IDX}` ON `{TABLE}`')
        elif vendor == 'sqlite':
            cursor.execute(f'DROP INDEX IF EXISTS "{IDX}"')
        elif vendor == 'postgresql':
            cursor.execute(f'DROP INDEX IF EXISTS "{IDX}"')


class Migration(migrations.Migration):

    atomic = False  # MySQL: создание индекса на большой таблице без блокировки всей миграции

    dependencies = [
        ('core', '0006_alter_globalapikeys_deepseek_verbose_name'),
        # Таблица TaskResult создаётся в django-celery-results; индекс — после неё.
        ('django_celery_results', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
