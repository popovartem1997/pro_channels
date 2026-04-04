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

    def _sub(pattern: str, repl: str):
        nonlocal s
        try:
            s = re.sub(pattern, repl, s)
        except re.error:
            # Best-effort: don't break import on malformed regex
            pass

    # Code blocks / inline code
    _sub(r"```([\s\S]*?)```", r"\1")
    _sub(r"`([^`\n]+?)`", r"\1")

    # Links: [text](url) -> text (url)
    _sub(r"\[([^\]]+?)\]\((https?://[^\s)]+)\)", r"\1 (\2)")

    # Bold/italic/underline/strike (common markdown)
    _sub(r"\*\*([^*\n]+?)\*\*", r"\1")
    _sub(r"__([^_\n]+?)__", r"\1")
    _sub(r"~~([^~\n]+?)~~", r"\1")
    _sub(r"\*([^*\n]+?)\*", r"\1")
    _sub(r"_([^_\n]+?)_", r"\1")

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


def _append_import_journal(
    run_id: int,
    message: str,
    *,
    step: int | None = None,
    step_total: int | None = None,
) -> None:
    """
    Журнал шагов импорта в progress_json.journal.
    step / step_total — для понятного UI (например «Шаг 3 из 7»).
    """
    try:
        with transaction.atomic():
            run = HistoryImportRun.objects.select_for_update().get(pk=run_id)
            p = dict(run.progress_json or {})
            log = list(p.get('journal') or [])
            entry = {
                't': timezone.now().isoformat(timespec='seconds'),
                'msg': (message or '')[:500],
            }
            if step is not None:
                entry['step'] = int(step)
            if step_total is not None:
                entry['step_total'] = int(step_total)
            log.append(entry)
            p['journal'] = log[-50:]
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

    def _on_telethon_lock_wait(elapsed_sec: float):
        _append_import_journal(
            run_id,
            f'Шаг 4: всё ещё жду доступ к сессии Telegram (~{int(elapsed_sec)} с). '
            'Параллельно сессию может держать парсинг ленты или другой импорт; при Redis-режиме зависший ключ '
            'истекает сам (TELETHON_REDIS_LOCK_TTL), либо снимите вручную: clear_telethon_session_locks / админка парсинга. '
            'При backend=file зависший процесс на сервере нужно остановить или перезапустить воркер.',
            step=4,
            step_total=7,
        )

    try:
        run = HistoryImportRun.objects.select_related('source_channel', 'target_channel').get(pk=run_id)
    except HistoryImportRun.DoesNotExist:
        return

    celery_tid = getattr(getattr(self, 'request', None), 'id', None) or ''
    logger.info('history_import run_id=%s celery_task_id=%s', run_id, celery_tid)

    if run.status in (HistoryImportRun.STATUS_DONE, HistoryImportRun.STATUS_CANCELLED):
        return

    if run.cancel_requested:
        _append_import_journal(run_id, 'Отмена: вы запросили остановку до начала работы воркера.', step=0, step_total=7)
        run.status = HistoryImportRun.STATUS_CANCELLED
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at'])
        return

    _append_import_journal(
        run_id,
        f'Воркер снял задачу из очереди и начал выполнение'
        f'{f" (идентификатор Celery: {celery_tid})" if celery_tid else ""}. '
        'Дальше: проверка пар каналов и ключей API.',
        step=1,
        step_total=7,
    )

    # Validate channels
    source = run.source_channel
    target = run.target_channel
    if source.platform != Channel.PLATFORM_TELEGRAM or target.platform != Channel.PLATFORM_MAX:
        _append_import_journal(
            run_id,
            'Ошибка: для импорта нужны два канала — источник Telegram и цель MAX.',
            step=2,
            step_total=7,
        )
        run.status = HistoryImportRun.STATUS_ERROR
        run.error_message = 'Некорректные каналы для импорта (нужен Telegram → MAX).'
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'finished_at'])
        return

    keys = get_global_api_keys()
    api_id = (keys.telegram_api_id or '').strip()
    api_hash = (keys.get_telegram_api_hash() or '').strip()
    if not api_id or not api_hash:
        _append_import_journal(
            run_id,
            'Ошибка: в разделе «Ключи API» не заполнены TELEGRAM_API_ID и TELEGRAM_API_HASH (нужны для Telethon).',
            step=2,
            step_total=7,
        )
        run.status = HistoryImportRun.STATUS_ERROR
        run.error_message = 'TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы (Ключи API → Парсинг Telegram).'
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'finished_at'])
        return

    _append_import_journal(
        run_id,
        'Проверка каналов и ключей пройдена: источник — Telegram, цель — MAX, API Telethon заданы.',
        step=2,
        step_total=7,
    )

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
            _append_import_journal(
                run_id,
                f'Ошибка: для этой пары каналов уже выполняется импорт #{competing.pk}. Дождитесь его окончания или остановите.',
                step=2,
                step_total=7,
            )
            run.status = HistoryImportRun.STATUS_ERROR
            run.error_message = f'Уже выполняется импорт #{competing.pk} для этой пары каналов.'
            run.finished_at = timezone.now()
            run.save(update_fields=['status', 'error_message', 'finished_at'])
            return
        run.status = HistoryImportRun.STATUS_RUNNING
        run.started_at = timezone.now()
        run.error_message = ''
        run.save(update_fields=['status', 'started_at', 'error_message'])

    _append_import_journal(
        run_id,
        'Проверки пройдены. Статус запуска: «В работе». Следующий шаг — занять общую сессию Telegram (как у парсинга) и читать сообщения.',
        step=3,
        step_total=7,
    )

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

    batch_limit = int(getattr(settings, 'TG_HISTORY_IMPORT_TELETHON_BATCH', 25) or 25)
    batch_limit = max(1, min(batch_limit, 200))

    async def _ensure_client_connected(client):
        if client is None:
            return
        try:
            if client.is_connected():
                return
        except Exception:
            pass
        for i in range(5):
            try:
                await client.connect()
                try:
                    if client.is_connected():
                        return
                except Exception:
                    return
            except Exception as exc:
                logger.warning('TG import: reconnect failed (attempt=%s): %s', i + 1, exc)
                await asyncio.sleep(1.5 + i * 1.7)

    async def _fetch_tg_import_batch(*, resume_after_id, take: int):
        """
        За один захват lock: подключение, чтение до take сообщений канала (с учётом resume_after_id),
        скачивание медиа. Публикация в MAX — снаружи, без lock.
        Возвращает (items, iterator_exhausted).
        """
        nonlocal errors
        from telethon import TelegramClient

        session_dir = settings.BASE_DIR / 'media' / 'telethon_sessions'
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        session_path = str(session_dir / f'user_{source.owner_id}')
        client = None
        items: list[dict] = []
        iterator_exhausted = False
        try:
            client = TelegramClient(session_path, int(api_id), api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                try:
                    await client.disconnect()
                except Exception:
                    pass
                client = TelegramClient(str(session_dir / 'user_default'), int(api_id), api_hash)
                await client.connect()
                if not await client.is_user_authorized():
                    raise ValueError(
                        'Telethon session не авторизована. '
                        'Подключите Telegram в UI (Парсинг → Подключить Telegram) или выполните '
                        '`python manage.py telethon_login` в контейнере web. '
                        'Важно: у celery должен быть смонтирован тот же /app/media.'
                    )

            await _ensure_client_connected(client)
            entity = await client.get_entity(_tg_entity_id_from_channel(source))
            # При каждом новом connect итератор снова с начала канала — продолжаем только через min_id.
            min_msg_id = 0
            if resume_after_id is not None:
                try:
                    min_msg_id = int(resume_after_id) + 1
                except (TypeError, ValueError):
                    min_msg_id = 0
            n = 0
            async for msg in client.iter_messages(entity, reverse=True, min_id=min_msg_id):
                if n >= take:
                    break
                if await _cancel_requested():
                    raise asyncio.CancelledError()
                await _ensure_client_connected(client)

                msg_id = getattr(msg, 'id', None)
                if msg_id is None:
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

                has_media = bool(getattr(msg, 'media', None))
                if not text and not has_media:
                    items.append({'kind': 'skip', 'msg_id': int(msg_id)})
                    n += 1
                    continue

                downloaded_paths: list[str] = []
                if has_media:
                    try:
                        await _ensure_client_connected(client)
                        media_root = Path(getattr(settings, 'MEDIA_ROOT', settings.BASE_DIR / 'media'))
                        rel_dir = Path('imports') / 'tg_to_max' / f'run_{run_id}' / f'msg_{msg_id}'
                        abs_dir = media_root / rel_dir
                        abs_dir.mkdir(parents=True, exist_ok=True)
                        base = abs_dir / 'media'
                        saved_path = await client.download_media(msg, file=str(base))
                        if saved_path:
                            downloaded_paths.append(str(saved_path))
                    except Exception as exc:
                        errors += 1
                        logger.warning('Import run=%s tg_msg=%s download_media error: %s', run_id, msg_id, exc)

                items.append(
                    {
                        'kind': 'post',
                        'msg_id': int(msg_id),
                        'text': text,
                        'paths': downloaded_paths[:10],
                        'media_type': _guess_media_type(msg),
                    }
                )
                n += 1
            else:
                iterator_exhausted = True
            return items, iterator_exhausted
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    async def _process_import_batch(batch: list[dict]):
        nonlocal sent, errors, last_tg_message_id
        for item in batch:
            if await _cancel_requested():
                raise asyncio.CancelledError()
            if item.get('kind') == 'skip':
                last_tg_message_id = int(item['msg_id'])
                await _update_progress_async(sent=sent, errors=errors, last_tg_message_id=last_tg_message_id)
                continue

            msg_id = int(item['msg_id'])
            text = item.get('text') or ''
            downloaded_paths = list(item.get('paths') or [])
            mt = item.get('media_type') or 'document'

            post_id = await _create_post_for_target(text)
            for i, p in enumerate(downloaded_paths[:10], start=1):
                try:
                    await _attach_file_to_post(post_id, p, i, mt)
                except Exception as exc:
                    errors += 1
                    logger.warning('Import run=%s tg_msg=%s attach error: %s', run_id, msg_id, exc)
            try:
                await _normalize_media(post_id)
            except Exception:
                pass

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
                    await asyncio.sleep(min(20.0, 1.2 + attempt * 2.2))

            if ok:
                sent += 1
                await _set_post_status(post_id, status='published', published_at=timezone.now())
            else:
                await _set_post_status(post_id, status='failed')
                logger.error('Import run=%s tg_msg=%s publish failed: %s', run_id, msg_id, last_exc)

            last_tg_message_id = msg_id
            await _update_progress_async(sent=sent, errors=errors, last_tg_message_id=last_tg_message_id)
            await asyncio.sleep(1.0)

    def _set_run_error_message(msg: str):
        try:
            HistoryImportRun.objects.filter(pk=run_id).update(error_message=(msg or '')[:2000])
        except Exception:
            pass

    # Telethon: lock только на порции чтения/скачивания из TG; публикация в MAX без lock —
    # парсинг и другие задачи могут занять сессию между порциями (см. TG_HISTORY_IMPORT_TELETHON_BATCH).
    try:
        lock_last_err = None
        lock_attempt = 0
        channel_done = False
        batch_phase_started = False
        journal_step4_logged = False

        while not channel_done:
            if HistoryImportRun.objects.filter(pk=run_id, cancel_requested=True).exists():
                raise asyncio.CancelledError()
            if HistoryImportRun.objects.filter(pk=run_id, status=HistoryImportRun.STATUS_CANCELLED).exists():
                raise asyncio.CancelledError()

            if not journal_step4_logged:
                _append_import_journal(
                    run_id,
                    'Шаг 4: ожидаю доступ к сессии Telegram (тот же замок, что и у парсинга). '
                    'Импорт читает канал порциями — между порциями lock отпускается. Долгое ожидание: активный парсинг, '
                    'зависший воркер или «осиротевший» Redis-ключ (см. TELETHON_REDIS_LOCK_TTL, clear_telethon_session_locks).',
                    step=4,
                    step_total=7,
                )
                journal_step4_logged = True

            try:
                tick = _on_telethon_lock_wait if not batch_phase_started else None
                with _telethon_session_lock(source.owner_id, on_lock_wait_tick=tick):
                    if not batch_phase_started:
                        _append_import_journal(
                            run_id,
                            f'Сессия свободна: читаю Telegram порциями по ~{batch_limit} сообщений, затем отпускаю lock для других задач.',
                            step=5,
                            step_total=7,
                        )
                        batch_phase_started = True
                    batch, exhausted = asyncio.run(
                        _fetch_tg_import_batch(resume_after_id=last_tg_message_id, take=batch_limit)
                    )
                lock_attempt = 0
                lock_last_err = None
            except RuntimeError as exc:
                msg = str(exc)
                lock_last_err = msg
                if 'не удалось занять сессию' in msg or 'не удалось занять' in msg or 'session' in msg.lower():
                    lock_attempt += 1
                    if lock_attempt >= 12:
                        raise
                    _set_run_error_message(
                        'Ожидаю освобождения Telegram-сессии (импорт истории или парсинг того же файла сессии). '
                        f'Повтор через 45с. Детали: {msg}'
                    )
                    _append_import_journal(
                        run_id,
                        f'Сессия занята другой задачей (парсинг или импорт). Пауза 45 с, попытка {lock_attempt} из 12.',
                        step=4,
                        step_total=7,
                    )
                    time.sleep(45)
                    continue
                raise

            if not batch:
                if exhausted:
                    channel_done = True
                continue

            asyncio.run(_process_import_batch(batch))

            if exhausted:
                channel_done = True

        # Пользователь мог нажать «Остановить» — не перезаписать cancelled обратно в done.
        fresh = HistoryImportRun.objects.get(pk=run_id)
        final_progress = {'sent': sent, 'errors': errors, 'last_tg_message_id': last_tg_message_id}
        now = timezone.now()
        if fresh.cancel_requested or fresh.status == HistoryImportRun.STATUS_CANCELLED:
            _append_import_journal(
                run_id,
                'Импорт остановлен по вашей команде.',
                step=7,
                step_total=7,
            )
            fresh = HistoryImportRun.objects.get(pk=run_id)
            pj = dict(fresh.progress_json or {})
            log = list(pj.get('journal') or [])
            pj.update(final_progress)
            pj['journal'] = log[-50:]
            fresh.status = HistoryImportRun.STATUS_CANCELLED
            fresh.finished_at = now
            fresh.progress_json = pj
            fresh.updated_at = now
            fresh.save(update_fields=['status', 'finished_at', 'progress_json', 'updated_at'])
        else:
            _append_import_journal(
                run_id,
                'Импорт завершён: сообщения обработаны, статус «Готово».',
                step=7,
                step_total=7,
            )
            fresh = HistoryImportRun.objects.get(pk=run_id)
            pj = dict(fresh.progress_json or {})
            log = list(pj.get('journal') or [])
            pj.update(final_progress)
            pj['journal'] = log[-50:]
            fresh.status = HistoryImportRun.STATUS_DONE
            fresh.finished_at = now
            fresh.progress_json = pj
            fresh.updated_at = now
            fresh.save(update_fields=['status', 'finished_at', 'progress_json', 'updated_at'])
    except asyncio.CancelledError:
        _append_import_journal(
            run_id,
            'Импорт прерван (отмена или остановка).',
            step=7,
            step_total=7,
        )
        run = HistoryImportRun.objects.get(pk=run_id)
        pj = dict(run.progress_json or {})
        log = list(pj.get('journal') or [])
        pj.update({'sent': sent, 'errors': errors, 'last_tg_message_id': last_tg_message_id})
        pj['journal'] = log[-50:]
        run.status = HistoryImportRun.STATUS_CANCELLED
        run.finished_at = timezone.now()
        run.progress_json = pj
        run.save(update_fields=['status', 'finished_at', 'progress_json'])
    except Exception as exc:
        _append_import_journal(
            run_id,
            f'Ошибка выполнения: {str(exc)[:400]}',
            step=6,
            step_total=7,
        )
        run = HistoryImportRun.objects.get(pk=run_id)
        pj = dict(run.progress_json or {})
        log = list(pj.get('journal') or [])
        pj.update({'sent': sent, 'errors': errors, 'last_tg_message_id': last_tg_message_id})
        pj['journal'] = log[-50:]
        run.status = HistoryImportRun.STATUS_ERROR
        run.finished_at = timezone.now()
        run.error_message = str(exc)
        run.progress_json = pj
        run.save(update_fields=['status', 'finished_at', 'error_message', 'progress_json'])
        logger.exception('Import run=%s failed: %s', run_id, exc)


@shared_task(ignore_result=True)
def channel_morning_digest_tick():
    """Периодически (Celery Beat): проверка утренних дайджестов по расписанию."""
    from channels.digest_services import tick_morning_digests

    tick_morning_digests()


@shared_task(ignore_result=True)
def channel_interesting_facts_tick():
    """Периодически: генерация черновиков «интересные факты» по расписанию."""
    from channels.facts_services import tick_interesting_facts

    tick_interesting_facts()

