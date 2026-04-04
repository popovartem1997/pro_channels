"""
Удаление файлов переноса TG→MAX и опционально файлов вложений постов MAX-канала.

Примеры:
  python manage.py purge_tg_import_media --imports-staging
  python manage.py purge_tg_import_media --imports-staging --strip-post-media-channel 42
  python manage.py purge_tg_import_media --strip-post-media-channel 42 --dry-run

После --strip-post-media-channel записи PostMedia в БД остаются, файлы с диска снимаются —
в интерфейсе отображается заглушка «Файл удалён» (см. PostMedia.file_is_available).
"""

from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from channels.models import Channel
from content.models import Post, PostMedia


class Command(BaseCommand):
    help = 'Очистка media/imports/tg_to_max и/или файлов вложений постов выбранного MAX-канала.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--imports-staging',
            action='store_true',
            help='Удалить всё содержимое media/imports/tg_to_max/ (промежуточные скачивания импорта).',
        )
        parser.add_argument(
            '--strip-post-media-channel',
            type=int,
            metavar='CHANNEL_ID',
            help='Удалить с диска файлы PostMedia у всех постов, привязанных к этому каналу '
            '(строки в БД сохраняются — в UI заглушка).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только показать, что было бы удалено.',
        )

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        staging = opts['imports_staging']
        ch_id = opts['strip_post_media_channel']

        if not staging and ch_id is None:
            raise CommandError('Укажите --imports-staging и/или --strip-post-media-channel ID')

        if staging:
            root = Path(settings.MEDIA_ROOT) / 'imports' / 'tg_to_max'
            if not root.is_dir():
                self.stdout.write(self.style.WARNING(f'Каталога нет: {root}'))
            else:
                n = sum(1 for _ in root.rglob('*') if _.is_file())
                self.stdout.write(f'Импорт (staging): {root} — файлов: {n}')
                if dry:
                    self.stdout.write(self.style.WARNING('dry-run: не удаляю'))
                else:
                    import shutil

                    shutil.rmtree(root, ignore_errors=True)
                    root.mkdir(parents=True, exist_ok=True)
                    self.stdout.write(self.style.SUCCESS('Каталог tg_to_max очищен.'))

        if ch_id is not None:
            try:
                ch = Channel.objects.get(pk=ch_id)
            except Channel.DoesNotExist as exc:
                raise CommandError(f'Канал #{ch_id} не найден') from exc
            if ch.platform != Channel.PLATFORM_MAX:
                self.stdout.write(
                    self.style.WARNING(
                        f'Канал #{ch_id} не MAX (platform={ch.platform!r}) — всё равно обрабатываю посты с этим каналом.'
                    )
                )

            posts = Post.objects.filter(channels=ch).distinct()
            pm_qs = PostMedia.objects.filter(post__in=posts)
            count = 0
            for pm in pm_qs.iterator():
                name = getattr(pm.file, 'name', '') or ''
                if not name:
                    continue
                if not default_storage.exists(name):
                    continue
                count += 1
                self.stdout.write(f'  {"[dry] " if dry else ""}delete storage: {name}')
                if not dry:
                    try:
                        default_storage.delete(name)
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'  ошибка {name}: {e}'))

            self.stdout.write(
                self.style.SUCCESS(
                    f'Вложения постов канала #{ch_id}: затронуто файлов на диске: {count}'
                    + (' (dry-run)' if dry else '')
                )
            )
