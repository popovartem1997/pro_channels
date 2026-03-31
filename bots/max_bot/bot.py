"""
MAX бот-предложка через MAX Bot API (https://dev.max.ru/).

MAX Bot API аналогичен Telegram Bot API:
  - Endpoint: https://botapi.max.ru
  - getUpdates / setWebhook для получения обновлений
  - sendMessage, sendPhoto, sendVideo для отправки
  - Callback кнопки (inline_keyboard) аналогичны Telegram

В данном боте реализован режим Long Polling (getUpdates).
Для production рекомендуется использовать Webhook через Django view.

Документация MAX Bot API: https://dev.max.ru/
"""
import logging
import time
import uuid as uuid_module

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

# Важно: Bot API переехал на platform-api.max.ru, токен передаётся через Authorization header.
# https://dev.max.ru/docs-api/methods/POST/messages
MAX_API_URL = 'https://platform-api.max.ru'

# Причины отклонения
REJECT_REASONS = [
    'Не по теме',
    'Спам или реклама',
    'Низкое качество',
    'Нарушение правил',
]


class MaxBotAPI:
    """Минимальный клиент для MAX Bot API."""

    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({'Authorization': token})
        self.session.headers.update({'Content-Type': 'application/json'})
        self.base = MAX_API_URL

    def call(self, method: str, params: dict | None = None) -> dict:
        """Выполнить GET-запрос к API."""
        url = f'{self.base}/{method}'
        try:
            resp = self.session.get(url, params=params or {}, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error('[MAX] Ошибка вызова %s: %s', method, e)
            return {}

    def post(self, method: str, json_data: dict, params: dict | None = None) -> dict:
        """Выполнить POST-запрос к API."""
        url = f'{self.base}/{method}'
        try:
            resp = self.session.post(url, params=params or {}, json=json_data, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error('[MAX] Ошибка POST %s: %s', method, e)
            return {}

    def get_updates(self, marker: int = None, limit: int = 100, timeout: int = 25) -> dict:
        """Получить новые события (long polling)."""
        params = {'limit': limit, 'timeout': timeout}
        if marker:
            params['marker'] = marker
        return self.call('updates', params=params)

    def get_message(self, message_id: str) -> dict:
        """Получить сообщение по mid."""
        try:
            mid = str(message_id).strip()
        except Exception:
            mid = ''
        if not mid:
            return {}
        return self.call(f'messages/{mid}', params={})

    def send_message(self, chat_id: str, text: str, attachments: list = None, buttons: list = None) -> dict:
        """Отправить текстовое сообщение."""
        payload = {
            'chat_id': chat_id,
            'text': text,
        }
        if attachments:
            payload['attachments'] = attachments
        if buttons:
            # Inline кнопки в формате MAX API
            payload['attachments'] = payload.get('attachments', []) + [{
                'type': 'inline_keyboard',
                'payload': {'buttons': buttons}
            }]
        # По API: chat_id передаётся как query параметр, не в теле.
        body = {'text': text}
        if payload.get('attachments'):
            body['attachments'] = payload['attachments']
        try:
            cid = int(chat_id)
        except Exception:
            cid = chat_id
        return self.post('messages', body, params={'chat_id': cid})

    def answer_callback(self, callback_id: str, text: str = None) -> dict:
        """Ответить на нажатие кнопки."""
        body: dict = {}
        if text:
            body['notification'] = text
        return self.post('answers', body, params={'callback_id': callback_id})

    def edit_message(self, message_id: str, text: str, buttons: list = None) -> dict:
        """Редактировать сообщение."""
        payload = {'text': text}
        if buttons:
            payload['attachments'] = [{
                'type': 'inline_keyboard',
                'payload': {'buttons': buttons}
            }]
        url = f'{self.base}/messages'
        try:
            resp = self.session.patch(url, json=payload, params={'message_id': message_id}, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error('[MAX] Ошибка edit_message: %s', e)
            return {}

    def set_webhook(self, url: str) -> dict:
        return self.post('subscriptions', {'url': url, 'update_types': ['message_created', 'bot_started', 'message_callback']})

    def delete_webhook(self) -> dict:
        url = f'{self.base}/subscriptions'
        try:
            resp = self.session.delete(url, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error('[MAX] Ошибка delete_webhook: %s', e)
            return {}


class MAXSuggestionBot:
    """
    Бот предложки для MAX социальной сети.
    Запускается через management-команду run_max_bots.
    """

    def __init__(self, bot_config):
        self.config = bot_config
        self.api = MaxBotAPI(bot_config.get_token())
        self._marker = None

    def run(self):
        """Запустить Long Polling цикл."""
        logger.info('[MAX] Запуск бота "%s"', self.config.name)
        while True:
            try:
                self._poll_once()
            except KeyboardInterrupt:
                logger.info('[MAX] Бот остановлен.')
                break
            except Exception as e:
                logger.exception('[MAX] Ошибка: %s', e)
                time.sleep(5)

    def _poll_once(self):
        """Один цикл получения обновлений."""
        data = self.api.get_updates(marker=self._marker, timeout=25)
        if not data:
            return

        # marker используется как "offset" — следующий запрос вернёт события после него
        if 'marker' in data:
            self._marker = data['marker']

        for update in data.get('updates', []):
            update_type = update.get('update_type', '')
            if update_type == 'bot_started':
                self._handle_start(update)
            elif update_type == 'message_created':
                self._handle_message(update)
            elif update_type == 'message_callback':
                self._handle_callback(update)

    def _handle_start(self, update: dict):
        """Пользователь нажал Start / написал первый раз."""
        chat_id = update.get('chat_id') or update.get('message', {}).get('recipient', {}).get('chat_id', '')
        if chat_id:
            self.api.send_message(str(chat_id), self.config.welcome_message, buttons=self._menu_buttons())

    def _menu_buttons(self):
        return [
            [
                {'type': 'callback', 'text': '📰 Прислать новость', 'payload': 'menu_send'},
                {'type': 'callback', 'text': '💬 Связаться с админом', 'payload': 'menu_contact'},
            ],
            [
                {'type': 'callback', 'text': '📬 Мои новости', 'payload': 'menu_my'},
                {'type': 'callback', 'text': '📊 Статистика', 'payload': 'menu_stats'},
            ],
        ]

    def _handle_message(self, update: dict):
        """Обработать входящее сообщение."""
        from bots.models import Suggestion, SuggestionUserStats
        from django.utils import timezone as tz
        from datetime import timedelta

        message = update.get('message', {})
        sender = message.get('sender', {})
        chat_id = message.get('recipient', {}).get('chat_id', '')
        user_id = str(sender.get('user_id', ''))
        text = message.get('body', {}).get('text', '')

        if not user_id:
            return

        # Команды
        if text.lower() in ('/start', 'start', 'начать'):
            self.api.send_message(chat_id, self.config.welcome_message, buttons=self._menu_buttons())
            return
        if text.lower() in ('/status', 'status', 'статус'):
            self._send_status(chat_id, user_id)
            return

        # Определяем тип и вложения
        content_type, media_ids = self._detect_content(message)
        if not content_type:
            self.api.send_message(chat_id, 'Извините, этот тип контента не поддерживается.')
            return

        # Сохраняем или "склеиваем" с последней pending заявкой (если пользователь догружает медиа)
        merge_window = tz.now() - timedelta(minutes=2)
        recent = (
            Suggestion.objects.filter(
                bot=self.config,
                platform_user_id=user_id,
                status=Suggestion.STATUS_PENDING,
                submitted_at__gte=merge_window,
            )
            .order_by('-submitted_at')
            .first()
        )
        if recent:
            # Merge
            merged_text = (recent.text or '').strip()
            new_text = (text or '').strip()
            if new_text:
                recent.text = (merged_text + '\n\n' + new_text).strip() if merged_text else new_text
            # media ids
            existing_ids = list(recent.media_file_ids or [])
            for mid in (media_ids or []):
                if mid and mid not in existing_ids:
                    existing_ids.append(mid)
            recent.media_file_ids = existing_ids
            # content type -> mixed if multiple parts
            if existing_ids and (recent.text or ''):
                recent.content_type = Suggestion.CONTENT_MIXED
            recent.raw_data = {
                'messages': (recent.raw_data.get('messages') if isinstance(recent.raw_data, dict) else []) + [message]
            }
            recent.submitted_at = tz.now()
            recent.save(update_fields=['text', 'media_file_ids', 'content_type', 'raw_data', 'submitted_at'])
            suggestion = recent
        else:
            suggestion = Suggestion.objects.create(
                bot=self.config,
                platform_user_id=user_id,
                platform_username=sender.get('username', ''),
                platform_first_name=sender.get('name', '').split()[0] if sender.get('name') else '',
                platform_last_name=' '.join(sender.get('name', '').split()[1:]) if sender.get('name') else '',
                content_type=content_type,
                text=text,
                media_file_ids=media_ids,
                raw_data=message,
            )

        stats, created = SuggestionUserStats.objects.get_or_create(
            bot=self.config,
            platform_user_id=user_id,
            defaults={
                'platform_username': sender.get('username', ''),
                'display_name': sender.get('name', '') or user_id,
            }
        )
        # Статистику увеличиваем только если это новая заявка, а не догрузка
        if not recent:
            stats.total += 1
            stats.pending += 1
        stats.last_submission = tz.now()
        stats.save()

        confirm = self.config.success_message.replace('{tracking_id}', suggestion.short_tracking_id)
        tracking_tag = f'#{suggestion.short_tracking_id}'
        if tracking_tag not in confirm:
            confirm = f'{tracking_tag}\n' + confirm
        self.api.send_message(chat_id, confirm, buttons=self._menu_buttons())

        # Переслать модераторам
        admin_chat_ids = []
        try:
            admin_chat_ids = self.config.get_moderation_chat_ids()
        except Exception:
            admin_chat_ids = [self.config.admin_chat_id] if self.config.admin_chat_id else []

        for cid in admin_chat_ids:
            if cid:
                self._forward_to_admin(suggestion, sender, text, admin_chat_id=str(cid))

    def _detect_content(self, message: dict):
        """Определить тип контента из сообщения MAX."""
        from bots.models import Suggestion

        body = message.get('body', {})
        text = body.get('text', '')
        attachments = body.get('attachments', [])
        media_ids = []
        content_type = None

        for att in attachments:
            att_type = att.get('type', '')
            payload = att.get('payload', {})
            token = payload.get('token') or payload.get('id', '')
            if token:
                media_ids.append(token)
            if att_type == 'image':
                content_type = Suggestion.CONTENT_PHOTO
            elif att_type == 'video':
                content_type = Suggestion.CONTENT_VIDEO
            elif att_type == 'audio':
                content_type = Suggestion.CONTENT_AUDIO
            elif att_type == 'file':
                content_type = Suggestion.CONTENT_DOCUMENT

        if not content_type and text:
            content_type = Suggestion.CONTENT_TEXT

        return content_type, media_ids

    def _forward_to_admin(self, suggestion, sender: dict, text: str, admin_chat_id: str):
        """Переслать заявку в чат модерации с кнопками."""
        name = sender.get('name', 'Неизвестно')
        username = sender.get('username', '')
        user_id = str(sender.get('user_id', ''))

        display = name
        if username:
            display += f' (@{username})'

        caption = (
            f'📬 Новое предложение #{suggestion.short_tracking_id}\n\n'
            f'👤 {display}\n'
            f'🆔 {user_id}\n'
            f'📎 Тип: {suggestion.get_content_type_display()}'
        )
        if text:
            preview = text[:300] + ('…' if len(text) > 300 else '')
            caption += f'\n\n📝 {preview}'

        uuid_str = str(suggestion.tracking_id)
        buttons = [[
            {
                'type': 'callback',
                'text': '✅ Одобрить',
                'payload': f'approve|{uuid_str}',
            },
            {
                'type': 'callback',
                'text': '❌ Отклонить',
                'payload': f'reject|{uuid_str}',
            },
        ]]

        self.api.send_message(admin_chat_id, caption, buttons=buttons)

    def _handle_callback(self, update: dict):
        """Обработать нажатие кнопки модератора."""
        from bots.models import Suggestion

        callback = update.get('callback', {}) if isinstance(update, dict) else {}
        callback_id = (callback.get('callback_id') or callback.get('id') or '') if isinstance(callback, dict) else ''
        payload = callback.get('payload', '') if isinstance(callback, dict) else ''
        # Иногда payload приходит объектом
        if isinstance(payload, dict):
            payload = payload.get('payload') or payload.get('data') or payload.get('text') or ''
        if payload is None:
            payload = ''
        payload = str(payload)
        user = callback.get('user', {})
        message = callback.get('message', {}) or {}
        message_id = message.get('mid', '')
        chat_id = message.get('recipient', {}).get('chat_id', '') or callback.get('chat_id', '')

        # User menu actions
        if payload in ('menu_send', 'menu_contact', 'menu_my', 'menu_stats'):
            self.api.answer_callback(callback_id)
            if payload == 'menu_send':
                self.api.send_message(str(chat_id), 'Отправьте новость одним сообщением (текст/фото/видео/файл).', buttons=self._menu_buttons())
                return
            if payload == 'menu_my':
                self._send_my_news(str(chat_id), str(user.get('user_id', '')))
                return
            if payload == 'menu_stats':
                self._send_status(str(chat_id), str(user.get('user_id', '')))
                return
            if payload == 'menu_contact':
                self._send_admin_contacts(str(chat_id), user)
                return

        if payload.startswith('approve|'):
            uuid_str = payload[8:]
            suggestion = self._get_suggestion(uuid_str)
            if not suggestion or suggestion.status != Suggestion.STATUS_PENDING:
                self.api.answer_callback(callback_id, 'Заявка уже обработана.')
                return
            suggestion.approve()
            notify = self.config.approved_message.replace('{tracking_id}', suggestion.short_tracking_id)
            self._notify_user(suggestion.platform_user_id, notify)
            self.api.answer_callback(callback_id, 'Одобрено!')
            mod_name = user.get('name', 'Модератор')
            if chat_id:
                self.api.send_message(str(chat_id), f'✅ Заявка #{suggestion.short_tracking_id} одобрена.\nМодератор: {mod_name}')

        elif payload.startswith('reject|'):
            uuid_str = payload[7:]
            # Показать кнопки с причинами
            buttons = [
                [{'type': 'callback', 'text': f'{i+1}. {r}', 'payload': f'rr|{uuid_str}|{i}'}]
                for i, r in enumerate(REJECT_REASONS)
            ]
            self.api.answer_callback(callback_id)
            if chat_id:
                self.api.send_message(str(chat_id), 'Выберите причину отклонения:', buttons=buttons)

        elif payload.startswith('rr|'):
            parts = payload.split('|', 2)
            if len(parts) < 3:
                return
            uuid_str, reason_idx = parts[1], int(parts[2])
            reason = REJECT_REASONS[reason_idx] if 0 <= reason_idx < len(REJECT_REASONS) else 'Не соответствует'
            suggestion = self._get_suggestion(uuid_str)
            if not suggestion or suggestion.status != Suggestion.STATUS_PENDING:
                self.api.answer_callback(callback_id, 'Заявка уже обработана.')
                return
            suggestion.reject(reason=reason)
            notify = (
                self.config.rejected_message
                .replace('{tracking_id}', suggestion.short_tracking_id)
                .replace('{reason}', reason)
            )
            self._notify_user(suggestion.platform_user_id, notify)
            self.api.answer_callback(callback_id, 'Отклонено.')
            mod_name = user.get('name', 'Модератор')
            if chat_id:
                self.api.send_message(
                    str(chat_id),
                    f'❌ Заявка #{suggestion.short_tracking_id} отклонена.\n'
                    f'Причина: {reason}\nМодератор: {mod_name}'
                )

    def _get_suggestion(self, uuid_str: str):
        from bots.models import Suggestion
        try:
            uid = uuid_module.UUID(uuid_str)
            return Suggestion.objects.get(tracking_id=uid, bot=self.config)
        except (Suggestion.DoesNotExist, ValueError):
            return None

    def _notify_user(self, user_id: str, text: str):
        """Отправить уведомление пользователю (через личку)."""
        # В MAX API нужен chat_id пользователя — для личных сообщений это user_id
        try:
            self.api.send_message(user_id, text)
        except Exception as e:
            logger.warning('[MAX] Не удалось уведомить %s: %s', user_id, e)

    def _send_status(self, chat_id: str, user_id: str):
        """Показать статистику пользователю."""
        from bots.models import SuggestionUserStats, Suggestion

        stats = SuggestionUserStats.objects.filter(
            bot=self.config, platform_user_id=user_id
        ).first()
        if not stats or stats.total == 0:
            self.api.send_message(chat_id, 'Вы ещё не отправляли предложений.')
            return

        recent = Suggestion.objects.filter(
            bot=self.config, platform_user_id=user_id
        ).order_by('-submitted_at')[:5]

        lines = [
            '📊 Ваша статистика:\n',
            f'📬 Всего: {stats.total}',
            f'✅ Одобрено: {stats.approved}',
            f'❌ Отклонено: {stats.rejected}',
            f'⏳ На модерации: {stats.pending}',
            f'📢 Опубликовано: {stats.published}',
            '',
            'Последние заявки:',
        ]
        for s in recent:
            lines.append(f'{s.status_emoji} #{s.short_tracking_id} — {s.get_status_display()}')

        self.api.send_message(chat_id, '\n'.join(lines), buttons=self._menu_buttons())

    def _send_my_news(self, chat_id: str, user_id: str):
        from bots.models import Suggestion
        items = Suggestion.objects.filter(bot=self.config, platform_user_id=str(user_id)).order_by('-submitted_at')[:10]
        if not items:
            text = 'У вас пока нет отправленных новостей. Нажмите «Прислать новость».'
        else:
            lines = ['📬 Ваши новости (последние 10):\n']
            for s in items:
                lines.append(f'{s.status_emoji} #{s.short_tracking_id} — {s.get_status_display()}')
            text = '\n'.join(lines)
        self.api.send_message(chat_id, text, buttons=self._menu_buttons())

    def _send_admin_contacts(self, chat_id: str, user: dict):
        # Show channel owner contacts from site
        channel = getattr(self.config, 'channel', None)
        owner = getattr(self.config, 'owner', None)
        site_nick = (getattr(channel, 'admin_contact_site', '') or '').strip()
        vk_nick = (getattr(channel, 'admin_contact_vk', '') or '').strip()
        max_phone = (getattr(channel, 'admin_contact_max_phone', '') or '').strip()
        if not site_nick:
            site_nick = getattr(owner, 'username', '') or ''
        lines = ['Контакты админа канала:']
        if site_nick:
            lines.append(f'— Сайт: {site_nick}')
        if vk_nick:
            lines.append(f'— VK: {vk_nick}')
        if max_phone:
            lines.append(f'— MAX (телефон): {max_phone}')
        if len(lines) == 1:
            lines.append('— Контакты не заполнены. Админ может добавить их в настройках канала.')
        self.api.send_message(chat_id, '\n'.join(lines), buttons=self._menu_buttons())

        # Log press
        try:
            from bots.models import AuditLog
            AuditLog.objects.create(
                actor=None,
                owner=owner,
                action='bot.contact_pressed',
                object_type='SuggestionBot',
                object_id=str(getattr(self.config, 'id', '')),
                data={
                    'channel_id': getattr(channel, 'id', None),
                    'platform': 'max',
                    'platform_user_id': str(user.get('user_id', '')),
                    'platform_username': str(user.get('username', '') or ''),
                }
            )
        except Exception:
            pass


def process_max_webhook(bot_config, update_data: dict):
    """
    Обработать одно обновление, пришедшее через Django webhook view.
    Используется вместо Long Polling в production.
    """
    bot = MAXSuggestionBot(bot_config)
    # MAX может присылать как одиночный update, так и батч.
    if isinstance(update_data, dict) and isinstance(update_data.get('updates'), list):
        for u in update_data.get('updates') or []:
            if isinstance(u, dict):
                process_max_webhook(bot_config, u)
        return

    # Реальность иногда отличается от доков: часть полей может быть внутри payload
    if isinstance(update_data, dict) and isinstance(update_data.get('payload'), dict):
        payload = update_data.get('payload') or {}
        merged = dict(update_data)
        for k in ('message', 'callback', 'chat_id', 'update_type', 'user_locale'):
            if k in payload and k not in merged:
                merged[k] = payload.get(k)
        update_data = merged

    update_type = (update_data or {}).get('update_type', '') if isinstance(update_data, dict) else ''

    if update_type == 'bot_started':
        bot._handle_start(update_data)
    elif update_type == 'message_created':
        bot._handle_message(update_data)
    elif update_type == 'message_callback':
        bot._handle_callback(update_data)
    else:
        # Best-effort fallback for alternative payload shapes
        if isinstance(update_data, dict):
            if update_data.get('callback'):
                bot._handle_callback(update_data)
            elif update_data.get('message'):
                bot._handle_message(update_data)
