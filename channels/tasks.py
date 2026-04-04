import logging

from celery import shared_task
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

    from .history_import_worker import execute_after_running

    execute_after_running(
        run_id,
        source=source,
        target=target,
    from .history_import_worker import execute_after_running

    execute_after_running(
        run_id,
        source=source,
        target=target,
        api_id=api_id,
        api_hash=api_hash,
        _append_import_journal=_append_import_journal,
        _update_progress=_update_progress,
        _on_telethon_lock_wait=_on_telethon_lock_wait,
        _telethon_session_lock=_telethon_session_lock,
        _publish_max=_publish_max,
    )

