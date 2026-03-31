"""
Сервисный слой для рекламных заказов.
"""

from __future__ import annotations

from datetime import datetime, time

from django.db import transaction
from django.utils import timezone


@transaction.atomic
def ensure_ad_post_for_order(order) -> int:
    """
    Создаёт (если ещё не создан) Post для рекламного заказа и планирует публикацию.

    Правила MVP:
    - пост создаётся один раз (order.post)
    - текст = order.description
    - ord_label = "Реклама" (включает авто-ОРД регистрацию в content.tasks после публикации)
    - каналы = order.channels
    - scheduled_at:
        - если order.start_date <= сегодня → публикуем сразу через Celery
        - иначе планируем на start_date 10:00 (Europe/Moscow), через STATUS_SCHEDULED
    - повтор:
        - если order.repeat_interval_days > 0 → включаем повтор каждые N дней до end_date
    """
    from content.models import Post

    order = order.__class__.objects.select_for_update().prefetch_related("channels").get(pk=order.pk)
    if order.post_id:
        return order.post_id

    channels = list(order.channels.all())
    if not channels:
        raise ValueError("Для рекламного заказа не выбраны каналы.")

    # Автор поста — владелец каналов (для MVP считаем, что каналы принадлежат одному владельцу).
    author = channels[0].owner

    # Планируем публикацию на начало кампании
    now = timezone.now()
    start_dt = timezone.make_aware(datetime.combine(order.start_date, time(10, 0)))

    if start_dt <= now:
        status = Post.STATUS_DRAFT
        scheduled_at = None
    else:
        status = Post.STATUS_SCHEDULED
        scheduled_at = start_dt

    repeat_interval_days = int(getattr(order, "repeat_interval_days", 0) or 0)
    repeat_enabled = repeat_interval_days > 0

    post = Post.objects.create(
        author=author,
        text=order.description,
        status=status,
        scheduled_at=scheduled_at,
        ord_label="Реклама",
        repeat_enabled=repeat_enabled,
        repeat_type=(Post.REPEAT_INTERVAL if repeat_enabled else Post.REPEAT_NONE),
        repeat_interval_days=(repeat_interval_days or 3),
        repeat_end_date=order.end_date,
        disable_notification=False,
        pin_message=False,
    )
    post.channels.set(channels)

    order.post = post
    order.save(update_fields=["post"])

    # Если уже пора — публикуем сразу
    if start_dt <= now:
        from content.tasks import publish_post_task
        publish_post_task.delay(post.pk)

    return post.pk

