import asyncio
import logging
import time
from pathlib import Path

from asgiref.sync import sync_to_async
from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from .models import Channel, HistoryImportRun

logger = logging.getLogger(__name__)

def _strip_simple_markdown(text: str) -> str:
    """
    Best-effort cleanup of Telegram/Markdown-style formatting artifacts so MAX gets plain text.
    Removes markers like **bold**, *italic*, __underline__, _italic_, ~~strike~~, `code`, ```code```,
    and converts [text](url) -> text (url).
    """
    import re

    s = str(text or '')
    if not s.strip():
        return ''

    # Code blocks / inline code
    s = re.sub(r"```([\\s\\S]*?)```", r"\\1", s)
    s = re.sub(r"`([^`\\n]+?)`", r"\\1", s)

    # Links: [text](url) -> text (url)
    s = re.sub(r"\\[([^\\]]+?)\\]\\((https?://[^\\s)]+)\\)", r"\\1 (\\2)", s)

    # Bold/italic/underline/strike (common markdown)
    s = re.sub(r"\\*\\*([^*\\n]+?)\\*\\*", r"\\1", s)
    s = re.sub(r"__([^_\\n]+?)__", r"\\1", s)
    s = re.sub(r"~~([^~\\n]+?)~~", r"\\1", s)
    s = re.sub(r"\\*([^*\\n]+?)\\*", r"\\1", s)
    s = re.sub(r"_([^_\\n]+?)_", r"\\1", s)

    # Cleanup stray markers
    s = s.replace('\\u2060', '')  # word-joiner
    s = re.sub(r"[\\t ]+", " ", s)
    s = re.sub(r"[ ]*\\n[ ]*", "\\n", s)
    return s.strip()


def _guess_media_type(message) -> str:
    try:
        if getattr(message, 'photo', None) is not None:
            return 'photo'
    except Exception:
        pass
    try:
        # telethon Message has .video for videos
        if getattr(message, 'video', None) is not None:
            return 'video'
    except Exception:
        pass
    try:
        doc = getattr(message, 'document', None)
        if doc is not None:
            mime = (getattr(doc, 'mime_type', '') or '').lower()
            if mime.startswith('video/'):
                return 'video'
    except Exception:
        pass
    return 'document'


def _tg_entity_id_from_channel(ch: Channel):
    """
    Для Telethon: используем tg_chat_id (может быть @username, t.me/..., число).
    """
    raw = (ch.tg_chat_id or '').strip()
    if not raw:
        # fallback: попробуем имя, если оно похоже на @username
        raw = (ch.name or '').strip()
    raw = (raw or '').strip()
    if raw.isdigit():
        try:
            return int(raw)
        except Exception:
            return raw
    return raw


def _truncate_max_text(s: str) -> str:
    s = (s or '').strip()
    if len(s) > 4000:
        return s[:3990].rstrip() + '…'
    return s


def _update_progress(run_id: int, *, sent=None, errors=None, last_tg_message_id=None):
    try:
        with transaction.atomic():
            run = HistoryImportRun.objects.select_for_update().get(pk=run_id)
            p = dict(run.progress_json or {})
            if sent is not None:
                p['sent'] = int(sent)
            if errors is not None:
                p['errors'] = int(errors)
            if last_tg_message_id is not None:
                p['last_tg_message_id'] = int(last_tg_message_id)
            run.progress_json = p
            run.save(update_fields=['progress_json', 'updated_at'])
    except Exception:
        pass


@shared_task(bind=True, max_retries=0)
def import_tg_history_to_max_task(self, run_id: int):
    """
    Telegram → MAX history import.
    """
    from core.models import get_global_api_keys
    from content.models import Post, PostMedia, PublishResult, normalize_post_media_orders
    from content.tasks import _publish_max
    from parsing.tasks import _telethon_session_lock

    try:
        run = HistoryImportRun.objects.select_related('source_channel', 'target_channel').get(pk=run_id)
    except HistoryImportRun.DoesNotExist:
        return

    if run.status in (HistoryImportRun.STATUS_DONE, HistoryImportRun.STATUS_CANCELLED):
        return

    if run.cancel_requested:
        run.status = HistoryImportRun.STATUS_CANCELLED
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at'])
        return

    # Validate channels
    source = run.source_channel
    target = run.target_channel
    if source.platform != Channel.PLATFORM_TELEGRAM or target.platform != Channel.PLATFORM_MAX:
        run.status = HistoryImportRun.STATUS_ERROR
        run.error_message = 'Некорректные каналы для импорта (нужен Telegram → MAX).'
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'finished_at'])
        return

    keys = get_global_api_keys()
    api_id = (keys.telegram_api_id or '').strip()
    api_hash = (keys.get_telegram_api_hash() or '').strip()
    if not api_id or not api_hash:
        run.status = HistoryImportRun.STATUS_ERROR
        run.error_message = 'TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы (Ключи API → Парсинг Telegram).'
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'finished_at'])
        return

    # Prepare run + guard against double-start (best-effort at worker side too)
    with transaction.atomic():
        run = HistoryImportRun.objects.select_for_update().select_related('source_channel', 'target_channel').get(pk=run_id)
        if run.cancel_requested:
            run.status = HistoryImportRun.STATUS_CANCELLED
            run.finished_at = timezone.now()
            run.save(update_fields=['status', 'finished_at'])
            return
        competing = HistoryImportRun.objects.select_for_update().filter(
            source_channel=run.source_channel,
            target_channel=run.target_channel,
            status=HistoryImportRun.STATUS_RUNNING,
        ).exclude(pk=run.pk).first()
        if competing:
            run.status = HistoryImportRun.STATUS_ERROR
            run.error_message = f'Уже выполняется импорт #{competing.pk} для этой пары каналов.'
            run.finished_at = timezone.now()
            run.save(update_fields=['status', 'error_message', 'finished_at'])
            return
        run.status = HistoryImportRun.STATUS_RUNNING
        run.started_at = timezone.now()
        run.error_message = ''
        run.save(update_fields=['status', 'started_at', 'error_message'])

    sent = int((run.progress_json or {}).get('sent') or 0)
    errors = int((run.progress_json or {}).get('errors') or 0)
    last_tg_message_id = (run.progress_json or {}).get('last_tg_message_id') or None

    @sync_to_async(thread_sensitive=True)
    def _update_progress_async(*, sent=None, errors=None, last_tg_message_id=None):
        _update_progress(run_id, sent=sent, errors=errors, last_tg_message_id=last_tg_message_id)

    @sync_to_async(thread_sensitive=True)
    def _cancel_requested() -> bool:
        try:
            rr = HistoryImportRun.objects.only('cancel_requested').get(pk=run_id)
            return bool(rr.cancel_requested)
        except Exception:
            return False

    @sync_to_async(thread_sensitive=True)
    def _create_post_for_target(text_value: str):
        from content.models import Post
        post = Post.objects.create(
            author=target.owner,
            published_by=run.created_by,
            text=_truncate_max_text(_strip_simple_markdown(text_value)),
            text_html='',
            status=Post.STATUS_DRAFT,
        )
        post.channels.add(target)
        return post.pk

    @sync_to_async(thread_sensitive=True)
    def _attach_file_to_post(post_id: int, file_path: str, order: int, media_type: str) -> bool:
        from content.models import PostMedia, Post
        pth = Path(file_path)
        if not pth.exists():
            return False
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return False
        with pth.open('rb') as f:
            PostMedia.objects.create(
                post=post,
                file=File(f, name=pth.name),
                media_type=media_type,
                order=int(order),
            )
        return True

    @sync_to_async(thread_sensitive=True)
    def _normalize_media(post_id: int):
        from content.models import Post, normalize_post_media_orders
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return
        normalize_post_media_orders(post)

    @sync_to_async(thread_sensitive=True)
    def _create_publish_result(post_id: int, ok: bool, platform_message_id: str = '', error_message: str = ''):
        from content.models import Post, PublishResult
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return
        PublishResult.objects.create(
            post=post,
            channel=target,
            status=PublishResult.STATUS_OK if ok else PublishResult.STATUS_FAIL,
            platform_message_id=platform_message_id or '',
            error_message=error_message or '',
        )

    @sync_to_async(thread_sensitive=True)
    def _set_post_status(post_id: int, *, status: str, published_at=None):
        from content.models import Post
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return
        post.status = status
        if published_at is not None:
            post.published_at = published_at
            post.save(update_fields=['status', 'published_at'])
        else:
            post.save(update_fields=['status'])

    @sync_to_async(thread_sensitive=True)
    def _publish_max_sync(post_id: int):
        from content.models import Post
        try:
            post = Post.objects.prefetch_related('media_files', 'channels').get(pk=post_id)
        except Post.DoesNotExist:
            # Пост мог быть удалён вручную во время импорта — пропускаем без падения всей задачи.
            raise RuntimeError('Post was deleted during import')
        return _publish_max(post, target)

    async def _do_import():
        from telethon import TelegramClient

        # Telethon sessions: same location as parsing.
        session_dir = settings.BASE_DIR / 'media' / 'telethon_sessions'
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        session_path = str(session_dir / f'user_{source.owner_id}')
        client = None
        try:
            client = TelegramClient(session_path, int(api_id), api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                try:
                    await client.disconnect()
                except Exception:
                    pass
                client = None
                default_path = str(session_dir / 'user_default')
                client = TelegramClient(default_path, int(api_id), api_hash)
                await client.connect()
                if not await client.is_user_authorized():
                    raise ValueError(
                        'Telethon session не авторизована. '
                        'Подключите Telegram в UI (Парсинг → Подключить Telegram) или выполните '
                        '`python manage.py telethon_login` в контейнере web. '
                        'Важно: у celery должен быть смонтирован тот же /app/media.'
                    )

            entity = await client.get_entity(_tg_entity_id_from_channel(source))
            # reverse=True: from oldest to newest
            async for msg in client.iter_messages(entity, reverse=True):
                nonlocal sent, errors, last_tg_message_id

                # cancellation: check every message
                if await _cancel_requested():
                    raise asyncio.CancelledError()

                msg_id = getattr(msg, 'id', None)
                if msg_id is None:
                    continue
                if last_tg_message_id is not None and int(msg_id) <= int(last_tg_message_id):
                    continue

                text = ''
                try:
                    text = (msg.text or '').strip()
                except Exception:
                    text = ''
                if not text:
                    try:
                        raw_txt = getattr(msg, 'raw_text', None)
                        if raw_txt is not None:
                            text = str(raw_txt).strip()
                    except Exception:
                        pass

                # If message has neither text nor media — skip
                has_media = bool(getattr(msg, 'media', None))
                if not text and not has_media:
                    last_tg_message_id = int(msg_id)
                    await _update_progress_async(sent=sent, errors=errors, last_tg_message_id=last_tg_message_id)
                    continue

                post_id = await _create_post_for_target(text)

                # Media: download one file per message (best-effort). MAX supports up to 10 attachments,
                # but Telegram message usually has one; albums may come as multiple messages.
                downloaded_paths = []
                if has_media:
                    try:
                        media_root = Path(getattr(settings, 'MEDIA_ROOT', settings.BASE_DIR / 'media'))
                        rel_dir = Path('imports') / 'tg_to_max' / f'run_{run_id}' / f'msg_{msg_id}'
                        abs_dir = media_root / rel_dir
                        abs_dir.mkdir(parents=True, exist_ok=True)
                        base = abs_dir / 'media'
                        saved_path = await client.download_media(msg, file=str(base))
                        if saved_path:
                            downloaded_paths.append(saved_path)
                    except Exception as exc:
                        errors += 1
                        logger.warning('Import run=%s tg_msg=%s download_media error: %s', run_id, msg_id, exc)

                # Attach to post
                for i, p in enumerate(downloaded_paths[:10], start=1):
                    try:
                        await _attach_file_to_post(post_id, p, i, _guess_media_type(msg))
                    except Exception as exc:
                        errors += 1
                        logger.warning('Import run=%s tg_msg=%s attach error: %s', run_id, msg_id, exc)
                try:
                    await _normalize_media(post_id)
                except Exception:
                    pass

                # Publish to MAX with retries/backoff
                ok = False
                last_exc = None
                for attempt in range(6):
                    try:
                        resp = await _publish_max_sync(post_id)
                        await _create_publish_result(
                            post_id,
                            True,
                            platform_message_id=str(resp.get('message_id', '')) if isinstance(resp, dict) else '',
                        )
                        ok = True
                        break
                    except Exception as exc:
                        last_exc = exc
                        errors += 1
                        await _create_publish_result(post_id, False, error_message=str(exc))
                        # Backoff: handle common throttling/connection resets.
                        await asyncio.sleep(min(20.0, 1.2 + attempt * 2.2))

                if ok:
                    sent += 1
                    await _set_post_status(post_id, status='published', published_at=timezone.now())
                else:
                    await _set_post_status(post_id, status='failed')
                    logger.error('Import run=%s tg_msg=%s publish failed: %s', run_id, msg_id, last_exc)

                last_tg_message_id = int(msg_id)
                await _update_progress_async(sent=sent, errors=errors, last_tg_message_id=last_tg_message_id)

                # Throttle for MAX API
                await asyncio.sleep(1.0)
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    try:
        with _telethon_session_lock(source.owner_id):
            asyncio.run(_do_import())
        run.status = HistoryImportRun.STATUS_DONE
        run.finished_at = timezone.now()
        run.progress_json = {'sent': sent, 'errors': errors, 'last_tg_message_id': last_tg_message_id}
        run.save(update_fields=['status', 'finished_at', 'progress_json'])
    except asyncio.CancelledError:
        run.status = HistoryImportRun.STATUS_CANCELLED
        run.finished_at = timezone.now()
        run.progress_json = {'sent': sent, 'errors': errors, 'last_tg_message_id': last_tg_message_id}
        run.save(update_fields=['status', 'finished_at', 'progress_json'])
    except Exception as exc:
        run.status = HistoryImportRun.STATUS_ERROR
        run.finished_at = timezone.now()
        run.error_message = str(exc)
        run.progress_json = {'sent': sent, 'errors': errors, 'last_tg_message_id': last_tg_message_id}
        run.save(update_fields=['status', 'finished_at', 'error_message', 'progress_json'])
        logger.exception('Import run=%s failed: %s', run_id, exc)

