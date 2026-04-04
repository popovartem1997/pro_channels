"""
Импорт истории Telegram → MAX внутри Celery.

Один внешний asyncio.run; Django/HTTP через asyncio.to_thread; чтение Telethon под lock —
в отдельном потоке с вложенным asyncio.run (отдельный цикл только для Telethon).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.utils import timezone

logger = logging.getLogger(__name__)


def execute_after_running(
    run_id: int,
    *,
    source,
    target,
    api_id: str,
    api_hash: str,
    _append_import_journal,
    _update_progress,
    _on_telethon_lock_wait,
    _telethon_session_lock,
    _publish_max,
) -> None:
    from channels import tasks as cht
    from channels.models import HistoryImportRun
    from content.models import Post, PostMedia, PublishResult
    from content.models import normalize_post_media_orders
    from django.db import close_old_connections

    run = HistoryImportRun.objects.get(pk=run_id)
    pj0 = run.progress_json or {}
    st = {
        'sent': int(pj0.get('sent') or 0),
        'errors': int(pj0.get('errors') or 0),
        'last_tg_message_id': pj0.get('last_tg_message_id'),
    }

    fetch_timeout = float(getattr(settings, 'TG_HISTORY_IMPORT_FETCH_TIMEOUT_SEC', 900) or 900)
    batch_limit = int(getattr(settings, 'TG_HISTORY_IMPORT_TELETHON_BATCH', 25) or 25)
    batch_limit = max(1, min(batch_limit, 200))

    def _cancel_req_sync() -> bool:
        try:
            rr = HistoryImportRun.objects.only('cancel_requested').get(pk=run_id)
            return bool(rr.cancel_requested)
        except Exception:
            return False

    def _create_post_sync(text_value: str) -> int:
        post = Post.objects.create(
            author=target.owner,
            published_by=run.created_by,
            text=cht._truncate_max_text(cht._strip_simple_markdown(text_value)),
            text_html='',
            status=Post.STATUS_DRAFT,
        )
        post.channels.add(target)
        return post.pk

    def _attach_file_to_post_sync(post_id: int, file_path: str, order: int, media_type: str) -> bool:
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

    def _normalize_media_sync(post_id: int) -> None:
        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist:
            return
        normalize_post_media_orders(post)

    def _create_publish_result_sync(
        post_id: int, ok: bool, platform_message_id: str = '', error_message: str = ''
    ) -> None:
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

    def _set_post_status_sync(post_id: int, *, status: str, published_at=None) -> None:
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

    def _publish_max_sync(post_id: int):
        try:
            post = Post.objects.prefetch_related('media_files', 'channels').get(pk=post_id)
        except Post.DoesNotExist:
            raise RuntimeError('Post was deleted during import') from None
        return _publish_max(post, target)

    def _set_run_error_message(msg: str) -> None:
        try:
            HistoryImportRun.objects.filter(pk=run_id).update(error_message=(msg or '')[:2000])
        except Exception:
            pass

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
        broken_early = False
        none_id_rows = 0
        raw_count = 0
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
            entity = await asyncio.wait_for(
                client.get_entity(cht._tg_entity_id_from_channel(source)),
                timeout=120.0,
            )
            min_msg_id = 0
            if resume_after_id is not None:
                try:
                    min_msg_id = int(resume_after_id) + 1
                except (TypeError, ValueError):
                    min_msg_id = 0

            async for msg in client.iter_messages(entity, reverse=True, min_id=min_msg_id, limit=take):
                if await asyncio.to_thread(_cancel_req_sync):
                    raise asyncio.CancelledError()
                await _ensure_client_connected(client)
                raw_count += 1

                msg_id = getattr(msg, 'id', None)
                if msg_id is None:
                    none_id_rows += 1
                    if none_id_rows > 200:
                        logger.warning(
                            'TG import run=%s: слишком много сообщений без id — завершаю чтение канала.',
                            run_id,
                        )
                        iterator_exhausted = True
                        broken_early = True
                        break
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
                        saved_path = await asyncio.wait_for(
                            client.download_media(msg, file=str(base)),
                            timeout=300.0,
                        )
                        if saved_path:
                            downloaded_paths.append(str(saved_path))
                    except Exception as exc:
                        st['errors'] += 1
                        logger.warning('Import run=%s tg_msg=%s download_media error: %s', run_id, msg_id, exc)

                if not text and not downloaded_paths:
                    items.append({'kind': 'skip', 'msg_id': int(msg_id)})
                    continue

                items.append(
                    {
                        'kind': 'post',
                        'msg_id': int(msg_id),
                        'text': text,
                        'paths': downloaded_paths[:10],
                        'media_type': cht._guess_media_type(msg),
                    }
                )

            if not broken_early:
                iterator_exhausted = raw_count < take

            return items, iterator_exhausted
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    async def _process_import_batch(batch: list[dict]) -> None:
        for item in batch:
            if await asyncio.to_thread(_cancel_req_sync):
                raise asyncio.CancelledError()
            if item.get('kind') == 'skip':
                st['last_tg_message_id'] = int(item['msg_id'])
                await asyncio.to_thread(
                    lambda: _update_progress(
                        run_id,
                        sent=st['sent'],
                        errors=st['errors'],
                        last_tg_message_id=st['last_tg_message_id'],
                    )
                )
                continue

            msg_id = int(item['msg_id'])
            text = item.get('text') or ''
            downloaded_paths = list(item.get('paths') or [])
            mt = item.get('media_type') or 'document'

            post_id = await asyncio.to_thread(_create_post_sync, text)
            for i, p in enumerate(downloaded_paths[:10], start=1):
                try:
                    await asyncio.to_thread(_attach_file_to_post_sync, post_id, p, i, mt)
                except Exception as exc:
                    st['errors'] += 1
                    logger.warning('Import run=%s tg_msg=%s attach error: %s', run_id, msg_id, exc)
            try:
                await asyncio.to_thread(_normalize_media_sync, post_id)
            except Exception:
                pass

            ok = False
            last_exc = None
            for attempt in range(6):
                try:
                    resp = await asyncio.to_thread(_publish_max_sync, post_id)
                    await asyncio.to_thread(
                        lambda r=resp, pid=post_id: _create_publish_result_sync(
                            pid,
                            True,
                            str(r.get('message_id', '')) if isinstance(r, dict) else '',
                            '',
                        )
                    )
                    ok = True
                    break
                except Exception as exc:
                    last_exc = exc
                    st['errors'] += 1
                    await asyncio.to_thread(
                        lambda pid=post_id, err=str(exc): _create_publish_result_sync(pid, False, '', err)
                    )
                    await asyncio.sleep(min(20.0, 1.2 + attempt * 2.2))

            if ok:
                st['sent'] += 1
                now_ts = timezone.now()
                await asyncio.to_thread(
                    lambda pid=post_id, ts=now_ts: _set_post_status_sync(
                        pid, status='published', published_at=ts
                    )
                )
            else:
                await asyncio.to_thread(lambda pid=post_id: _set_post_status_sync(pid, status='failed'))
                logger.error('Import run=%s tg_msg=%s publish failed: %s', run_id, msg_id, last_exc)
                err_txt = str(last_exc) if last_exc else ''
                _append_import_journal(
                    run_id,
                    f'Ошибка публикации в MAX (TG msg {msg_id}): {err_txt[:400]}',
                    step=6,
                    step_total=7,
                )

            st['last_tg_message_id'] = msg_id
            await asyncio.to_thread(
                lambda: _update_progress(
                    run_id,
                    sent=st['sent'],
                    errors=st['errors'],
                    last_tg_message_id=st['last_tg_message_id'],
                )
            )
            await asyncio.sleep(1.0)

    async def _async_main() -> None:
        lock_attempt = 0
        channel_done = False
        ph = {'batch_phase_started': False}
        j4 = {'logged': False}

        def _sync_fetch_locked():
            from django.db import close_old_connections as _close

            try:
                tick = _on_telethon_lock_wait if not ph['batch_phase_started'] else None
                with _telethon_session_lock(source.owner_id, on_lock_wait_tick=tick):
                    if not ph['batch_phase_started']:
                        _append_import_journal(
                            run_id,
                            f'Сессия свободна: читаю Telegram порциями по ~{batch_limit} сообщений, '
                            'затем отпускаю lock для других задач.',
                            step=5,
                            step_total=7,
                        )
                        ph['batch_phase_started'] = True
                    try:
                        return asyncio.run(
                            asyncio.wait_for(
                                _fetch_tg_import_batch(
                                    resume_after_id=st['last_tg_message_id'],
                                    take=batch_limit,
                                ),
                                timeout=fetch_timeout,
                            )
                        )
                    except asyncio.TimeoutError as exc:
                        raise ValueError(
                            f'Таймаут чтения Telegram ({int(fetch_timeout)} с). '
                            'Уменьшите TG_HISTORY_IMPORT_TELETHON_BATCH или повторите позже.'
                        ) from exc
            finally:
                _close()

        while not channel_done:
            if HistoryImportRun.objects.filter(pk=run_id, cancel_requested=True).exists():
                raise asyncio.CancelledError()
            if HistoryImportRun.objects.filter(pk=run_id, status=HistoryImportRun.STATUS_CANCELLED).exists():
                raise asyncio.CancelledError()

            if not j4['logged']:
                _append_import_journal(
                    run_id,
                    'Шаг 4: ожидаю доступ к сессии Telegram (тот же замок, что и у парсинга). '
                    'Импорт читает канал порциями — между порциями lock отпускается. Долгое ожидание: активный парсинг, '
                    'зависший воркер или «осиротевший» Redis-ключ (см. TELETHON_REDIS_LOCK_TTL, clear_telethon_session_locks).',
                    step=4,
                    step_total=7,
                )
                j4['logged'] = True

            try:
                batch, exhausted = await asyncio.to_thread(_sync_fetch_locked)
            except RuntimeError as exc:
                msg = str(exc)
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
                    await asyncio.sleep(45)
                    continue
                raise

            lock_attempt = 0
            posts_in_batch = sum(1 for x in batch if x.get('kind') == 'post')
            skips_in_batch = sum(1 for x in batch if x.get('kind') == 'skip')
            _append_import_journal(
                run_id,
                f'Шаг 5: из Telegram прочитано {len(batch)} позиций (к публикации: {posts_in_batch}, пропуски: {skips_in_batch}). '
                f'Уже в MAX: {st["sent"]}, ошибок: {st["errors"]}.',
                step=5,
                step_total=7,
            )

            if not batch:
                if exhausted:
                    channel_done = True
                continue

            await _process_import_batch(batch)
            _append_import_journal(
                run_id,
                f'Шаг 6: порция обработана; всего опубликовано в MAX: {st["sent"]}, ошибок: {st["errors"]}, '
                f'последний TG id: {st["last_tg_message_id"]}.',
                step=6,
                step_total=7,
            )

            if exhausted:
                channel_done = True
            close_old_connections()

        fresh = HistoryImportRun.objects.get(pk=run_id)
        final_progress = {
            'sent': st['sent'],
            'errors': st['errors'],
            'last_tg_message_id': st['last_tg_message_id'],
        }
        now = timezone.now()
        if fresh.cancel_requested or fresh.status == HistoryImportRun.STATUS_CANCELLED:
            _append_import_journal(run_id, 'Импорт остановлен по вашей команде.', step=7, step_total=7)
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

    try:
        asyncio.run(_async_main())
    except asyncio.CancelledError:
        _append_import_journal(run_id, 'Импорт прерван (отмена или остановка).', step=7, step_total=7)
        run_o = HistoryImportRun.objects.get(pk=run_id)
        pj = dict(run_o.progress_json or {})
        log = list(pj.get('journal') or [])
        pj.update(
            {
                'sent': st['sent'],
                'errors': st['errors'],
                'last_tg_message_id': st['last_tg_message_id'],
            }
        )
        pj['journal'] = log[-50:]
        run_o.status = HistoryImportRun.STATUS_CANCELLED
        run_o.finished_at = timezone.now()
        run_o.progress_json = pj
        run_o.save(update_fields=['status', 'finished_at', 'progress_json'])
    except Exception as exc:
        _append_import_journal(
            run_id,
            f'Ошибка выполнения: {str(exc)[:400]}',
            step=6,
            step_total=7,
        )
        run_o = HistoryImportRun.objects.get(pk=run_id)
        pj = dict(run_o.progress_json or {})
        log = list(pj.get('journal') or [])
        pj.update(
            {
                'sent': st['sent'],
                'errors': st['errors'],
                'last_tg_message_id': st['last_tg_message_id'],
            }
        )
        pj['journal'] = log[-50:]
        run_o.status = HistoryImportRun.STATUS_ERROR
        run_o.finished_at = timezone.now()
        run_o.error_message = str(exc)
        run_o.progress_json = pj
        run_o.save(update_fields=['status', 'finished_at', 'error_message', 'progress_json'])
        logger.exception('Import run=%s failed: %s', run_id, exc)
