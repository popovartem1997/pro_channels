"""
Celery задачи для сбора статистики с платформ.

sync_channel_stats — собирает данные о подписчиках каналов (TG, VK, MAX).
sync_post_stats — собирает просмотры/реакции опубликованных постов.
"""
import logging
from datetime import timedelta
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def sync_channel_stats():
    """Собирает статистику каналов: подписчики, просмотры за день, ER, посты за день."""
    from django.db.models import Sum

    from channels.models import Channel
    from content.models import PublishResult
    from .models import ChannelStat, PostStat

    today = timezone.localdate()
    channels = Channel.objects.filter(is_active=True)
    synced = 0

    for channel in channels:
        try:
            subscribers = _get_subscribers_count(channel)
            if subscribers is None:
                continue

            channel.subscribers_count = subscribers
            channel.last_synced_at = timezone.now()
            channel.save(update_fields=['subscribers_count', 'last_synced_at'])

            post_stats_qs = PostStat.objects.filter(channel=channel)
            total_views_sum = post_stats_qs.aggregate(s=Sum('views'))['s'] or 0

            prev = ChannelStat.objects.filter(channel=channel, date__lt=today).order_by('-date').first()
            prev_cum = prev.cumulative_post_views if prev else 0
            daily_views = max(0, int(total_views_sum) - int(prev_cum))

            er_vals = []
            for p in post_stats_qs.iterator(chunk_size=200):
                v = int(p.views or 0)
                if v <= 0:
                    continue
                actions = int(p.reactions or 0) + int(p.comments or 0) + int(p.forwards or 0)
                er_vals.append(round(actions / v * 100, 2))
            avg_er = round(sum(er_vals) / len(er_vals), 2) if er_vals else 0.0

            posts_today = PublishResult.objects.filter(
                channel=channel,
                status=PublishResult.STATUS_OK,
                published_at__date=today,
            ).count()

            ChannelStat.objects.update_or_create(
                channel=channel,
                date=today,
                defaults={
                    'subscribers': subscribers,
                    'views': daily_views,
                    'cumulative_post_views': int(total_views_sum),
                    'er': avg_er,
                    'posts_count': posts_today,
                },
            )
            synced += 1
        except Exception as exc:
            logger.error(f'Ошибка синхронизации статистики канала {channel.name}: {exc}')

    logger.info(f'Статистика каналов синхронизирована: {synced}/{channels.count()}')
    return synced


def _get_subscribers_count(channel):
    """Получает количество подписчиков канала через API платформы."""
    import requests
    from channels.models import Channel as Ch

    if channel.platform == Ch.PLATFORM_TELEGRAM:
        token = channel.get_tg_token()
        chat_id = channel.tg_chat_id
        if not token or not chat_id:
            return None
        resp = requests.get(
            f'https://api.telegram.org/bot{token}/getChatMemberCount',
            params={'chat_id': chat_id},
            timeout=10,
        )
        data = resp.json()
        if data.get('ok'):
            return data['result']

    elif channel.platform == Ch.PLATFORM_VK:
        token = channel.get_vk_token()
        group_id = channel.vk_group_id
        if not token or not group_id:
            return None
        resp = requests.get(
            'https://api.vk.com/method/groups.getById',
            params={
                'access_token': token,
                'group_id': abs(int(group_id)),
                'fields': 'members_count',
                'v': '5.131',
            },
            timeout=10,
        )
        data = resp.json()
        groups = data.get('response', [])
        if groups:
            return groups[0].get('members_count', 0)

    elif channel.platform == Ch.PLATFORM_MAX:
        token = channel.get_max_token()
        channel_id = channel.max_channel_id
        if not token or not channel_id:
            return None
        chat_id_raw = str(channel_id).strip()
        try:
            chat_id = int(chat_id_raw)
        except Exception:
            chat_id = chat_id_raw
        resp = requests.get(
            f'https://platform-api.max.ru/chats/{chat_id}',
            headers={'Authorization': token},
            timeout=10,
        )
        try:
            data = resp.json()
        except Exception:
            return None
        if resp.status_code >= 400 or (isinstance(data, dict) and data.get('code')):
            raise ValueError(f'MAX API error (chat_id={chat_id_raw}, http={resp.status_code}): {data}')
        if isinstance(data, dict):
            return data.get('participants_count', 0)

    elif channel.platform == Ch.PLATFORM_INSTAGRAM:
        token = channel.get_ig_token()
        account_id = channel.ig_account_id
        if not token or not account_id:
            return None
        resp = requests.get(
            f'https://graph.facebook.com/v19.0/{account_id}',
            params={'access_token': token, 'fields': 'followers_count'},
            timeout=10,
        )
        data = resp.json()
        return data.get('followers_count')

    return None


@shared_task
def sync_post_stats():
    """Собирает статистику опубликованных постов (просмотры, реакции).

    Обрабатывает посты, опубликованные за последние 120 дней (под периоды 7/30/90).
    """
    from content.models import Post, PublishResult
    from .models import PostStat, PostStatSnapshot

    cutoff = timezone.now() - timedelta(days=120)
    results = PublishResult.objects.filter(
        status=PublishResult.STATUS_OK,
        post__published_at__gte=cutoff,
    ).select_related('post', 'channel')

    synced = 0
    for result in results:
        try:
            stats = _get_post_stats(result)
            if stats is None:
                continue

            post_stat, _ = PostStat.objects.update_or_create(
                post=result.post,
                channel=result.channel,
                defaults=stats,
            )

            # Сохраняем снимок для графика динамики
            PostStatSnapshot.objects.create(
                post=result.post,
                channel=result.channel,
                **stats,
            )
            synced += 1
        except Exception as exc:
            logger.error(f'Ошибка синхронизации статистики поста #{result.post.pk}: {exc}')

    logger.info(f'Статистика постов синхронизирована: {synced} записей')
    return synced


def _get_post_stats(publish_result):
    """Получает статистику поста через API платформы."""
    import requests
    from channels.models import Channel as Ch

    channel = publish_result.channel
    msg_id = publish_result.platform_message_id

    if not msg_id:
        return None

    if channel.platform == Ch.PLATFORM_TELEGRAM:
        # Telegram Bot API не предоставляет статистику постов напрямую.
        # Для получения статистики используется getMessages (недоступен ботам).
        # Можно использовать Telethon для получения просмотров.
        return _get_tg_post_stats(channel, msg_id)

    elif channel.platform == Ch.PLATFORM_VK:
        token = channel.get_vk_token()
        group_id = channel.vk_group_id
        if not token or not group_id:
            return None
        owner_id = f'-{abs(int(group_id))}'
        try:
            resp = requests.get(
                'https://api.vk.com/method/wall.getById',
                params={
                    'access_token': token,
                    'posts': f'{owner_id}_{msg_id}',
                    'v': '5.131',
                },
                timeout=10,
            )
            data = resp.json()
            items = data.get('response', [])
            if items:
                post = items[0]
                return {
                    'views': post.get('views', {}).get('count', 0),
                    'reactions': post.get('likes', {}).get('count', 0),
                    'forwards': post.get('reposts', {}).get('count', 0),
                    'comments': post.get('comments', {}).get('count', 0),
                }
        except Exception as exc:
            logger.error(f'VK wall.getById ошибка: {exc}')

    elif channel.platform == Ch.PLATFORM_MAX:
        return _get_max_post_stats(channel, msg_id)

    return None


def _get_max_post_stats(channel, msg_id):
    """Статистика поста в канале MAX: GET /messages/{mid}, поле stat (документация dev.max.ru — Message.stat)."""
    from bots.max_bot.bot import MaxBotAPI

    token = channel.get_max_token()
    if not token or not str(msg_id).strip():
        return None
    try:
        api = MaxBotAPI(token)
        data = api.get_message(str(msg_id).strip())
    except Exception as exc:
        logger.error('MAX get_message ошибка: %s', exc)
        return None
    if not isinstance(data, dict) or data.get('code'):
        return None

    msg = data
    if isinstance(data.get('message'), dict):
        msg = data['message']
    stat = msg.get('stat') or msg.get('stats') or {}
    if not isinstance(stat, dict):
        stat = {}

    def _int(v, default=0):
        try:
            if v is None:
                return default
            if isinstance(v, dict):
                return int(v.get('count', v.get('value', default)) or default)
            return int(v)
        except Exception:
            return default

    views = _int(stat.get('views'))
    if not views:
        for k in ('view_count', 'views_count', 'total_views'):
            views = _int(stat.get(k))
            if views:
                break
    reactions = _int(stat.get('likes')) + _int(stat.get('reactions'))
    if not reactions:
        reactions = _int(stat.get('reactions_count')) + _int(stat.get('likes_count'))
    forwards = _int(stat.get('shares')) + _int(stat.get('forwards'))
    comments = _int(stat.get('comments'))

    if not any((views, reactions, forwards, comments)):
        return None

    return {
        'views': views,
        'reactions': reactions,
        'forwards': forwards,
        'comments': comments,
    }


def _get_tg_post_stats(channel, msg_id):
    """Получает статистику поста Telegram через Telethon (если настроен).

    Тот же каталог сессий, что и парсинг (media/telethon_sessions/user_{owner_id}).
    Нельзя вызывать client.start() без сессии: в Celery нет stdin → «EOF when reading a line».
    """
    from django.conf import settings
    import asyncio

    try:
        from core.models import get_global_api_keys
        keys = get_global_api_keys()
        api_id = (keys.telegram_api_id or '').strip()
        api_hash = (keys.get_telegram_api_hash() or '').strip()
    except Exception:
        api_id = getattr(settings, 'TELEGRAM_API_ID', '')
        api_hash = getattr(settings, 'TELEGRAM_API_HASH', '')
    if not api_id or not api_hash:
        return None

    async def _fetch():
        from telethon import TelegramClient

        session_dir = settings.BASE_DIR / 'media' / 'telethon_sessions'
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        paths = []
        oid = getattr(channel, 'owner_id', None)
        if oid:
            paths.append(session_dir / f'user_{int(oid)}')
        paths.append(session_dir / 'user_default')

        client = None
        for base in paths:
            c = TelegramClient(str(base), int(api_id), api_hash)
            await c.connect()
            if await c.is_user_authorized():
                client = c
                break
            await c.disconnect()

        if client is None:
            logger.warning(
                'TG post stats: нет авторизованной Telethon-сессии для канала pk=%s (пробовали %s)',
                getattr(channel, 'pk', None),
                [str(p) for p in paths],
            )
            return None

        try:
            entity = await client.get_entity(channel.tg_chat_id)
            msgs = await client.get_messages(entity, ids=[int(msg_id)])
            if msgs and msgs[0]:
                msg = msgs[0]
                return {
                    'views': msg.views or 0,
                    'reactions': sum(r.count for r in (msg.reactions.results if msg.reactions else [])),
                    'forwards': msg.forwards or 0,
                    'comments': msg.replies.replies if msg.replies else 0,
                }
        finally:
            await client.disconnect()
        return None

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()
