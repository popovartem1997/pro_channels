"""
Периодическая отправка статистики ОРД за предыдущий календарный месяц.
Расписание: CELERY_BEAT_SCHEDULE (1-е число, ~04:15 МСК).
"""
from datetime import date

from celery import shared_task


@shared_task(ignore_result=True)
def submit_previous_month_ord_statistics():
    from ord_marking.models import ORDRegistration
    from ord_marking.services import submit_statistics_for_month
    from core.models import get_global_api_keys

    keys = get_global_api_keys()
    sandbox = bool(getattr(keys, 'vk_ord_use_sandbox', False))

    today = date.today()
    if today.month == 1:
        y, m = today.year - 1, 12
    else:
        y, m = today.year, today.month - 1

    qs = ORDRegistration.objects.filter(
        status=ORDRegistration.STATUS_REGISTERED,
    ).exclude(erid='').exclude(ord_token='')

    for reg in qs.iterator():
        try:
            submit_statistics_for_month(reg, y, m, use_sandbox=sandbox)
        except Exception:
            continue


@shared_task(ignore_result=True)
def sync_ord_catalog_task(run_id: int):
    """Фоновая синхронизация контрагентов/договоров из ОРД."""
    from django.utils import timezone
    from ord_marking.models import OrdSyncRun
    from advertisers.services import sync_advertisers_and_contracts_from_ord
    from core.models import get_global_api_keys

    run = OrdSyncRun.objects.filter(pk=run_id).first()
    if not run:
        return
    keys = get_global_api_keys()
    sandbox = bool(getattr(keys, 'vk_ord_use_sandbox', False))
    run.status = OrdSyncRun.STATUS_RUNNING
    run.started_at = timezone.now()
    run.error_message = ''
    run.save(update_fields=['status', 'started_at', 'error_message'])

    try:
        res = sync_advertisers_and_contracts_from_ord(use_sandbox=sandbox)
        if not res.get('ok'):
            run.status = OrdSyncRun.STATUS_ERROR
            run.error_message = (res.get('error') or 'Ошибка синхронизации')[:2000]
            run.result = res
        else:
            run.status = OrdSyncRun.STATUS_DONE
            run.result = res
    except Exception as e:
        run.status = OrdSyncRun.STATUS_ERROR
        run.error_message = str(e)[:2000]
    run.finished_at = timezone.now()
    run.save(update_fields=['status', 'result', 'error_message', 'finished_at'])
