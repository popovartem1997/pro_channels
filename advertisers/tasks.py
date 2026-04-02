"""
Celery: обслуживание рекламных заявок (статусы, акты).
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def ad_campaigns_maintenance():
    """
    Обновляет статусы заявок (все посты опубликованы → published),
    по истечении срока размещения канала создаёт акт и completed.
    """
    from content.models import Post
    from advertisers.models import AdApplication, Act
    from billing.pdf import generate_act_pdf

    now = timezone.now()
    for app in AdApplication.objects.filter(
        status__in=[
            AdApplication.STATUS_SCHEDULED,
            AdApplication.STATUS_PAID,
            AdApplication.STATUS_PUBLISHED,
        ]
    ).select_related('channel'):
        posts = list(app.campaign_posts.all())
        if not posts:
            continue
        total = len(posts)
        pub = sum(1 for p in posts if p.status == Post.STATUS_PUBLISHED)
        if pub == total and total > 0:
            if app.status != AdApplication.STATUS_PUBLISHED:
                AdApplication.objects.filter(pk=app.pk).update(
                    status=AdApplication.STATUS_PUBLISHED,
                    updated_at=now,
                )
            last_pub = max((p.published_at for p in posts if p.published_at), default=None)
            if last_pub:
                days = int(getattr(app.channel, 'ad_post_lifetime_days', None) or 7)
                deadline = last_pub + timedelta(days=days)
                if now >= deadline:
                    if not Act.objects.filter(ad_application=app).exists():
                        act = Act.objects.create(
                            ad_application=app,
                            order=None,
                            amount=app.total_amount,
                            service_description=f'Размещение рекламы, заявка #{app.pk}, канал «{app.channel.name}»',
                            issued_at=now.date(),
                        )
                        try:
                            generate_act_pdf(act)
                        except Exception as e:
                            logger.warning('PDF акта заявки %s: %s', app.pk, e)

                    from content.tasks import delete_published_post_from_network

                    for p in list(
                        Post.objects.filter(
                            campaign_application=app,
                            status=Post.STATUS_PUBLISHED,
                        )
                    ):
                        try:
                            delete_published_post_from_network(p)
                        except Exception as e:
                            logger.warning('Снятие рекламы с площадок (пост %s): %s', p.pk, e)
                        try:
                            p.delete()
                        except Exception as e:
                            logger.exception('Удаление поста %s из БД: %s', p.pk, e)

                    AdApplication.objects.filter(pk=app.pk).update(
                        status=AdApplication.STATUS_COMPLETED,
                        updated_at=now,
                    )
