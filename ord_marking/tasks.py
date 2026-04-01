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
