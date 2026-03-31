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

MAX_API_URL = 'https://botapi.max.ru'

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
        self.session.params = {'access_token': token}
        self.base = MAX_API_URL

    def call(self, method: str, **params) -> dict:
        """Выполнить GET-запрос к API."""
        url = f'{self.base}/{method}'
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error('[MAX] Ошибка вызова %s: %s', method, e)
            return {}

    def post(self, method: str, json_data: dict) -> dict:
        """Выполнить POST-запрос к API."""
        url = f'{self.base}/{method}'
        try:
            resp = self.session.post(url, json=json_data, timeout=30)
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
        return self.call('updates', **params)

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
        return self.post('messages', payload)

    def answer_callback(self, callback_id: str, text: str = None) -> dict:
        """Ответить на нажатие кнопки."""
        payload = {'callback_id': callback_id}
        if text:
            payload['notification'] = text
        return self.post('answers', payload)

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
            self.api.send_message(str(chat_id), self.config.welcome_message)

    def _handle_message(self, update: dict):
        """Обработать входящее сообщение."""
        from bots.models import Suggestion, SuggestionUserStats

        message = update.get('message', {})
        sender = message.get('sender', {})
        chat_id = message.get('recipient', {}).get('chat_id', '')
        user_id = str(sender.get('user_id', ''))
        text = message.get('body', {}).get('text', '')

        if not user_id:
            return

        # Команды
        if text.lower() in ('/start', 'start', 'начать'):
            self.api.send_message(chat_id, self.config.welcome_message)
            return
        if text.lower() in ('/status', 'status', 'статус'):
            self._send_status(chat_id, user_id)
            return

        # Определяем тип и вложения
        content_type, media_ids = self._detect_content(message)
        if not content_type:
            self.api.send_message(chat_id, 'Извините, этот тип контента не поддерживается.')
            return

        # Сохраняем
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
        stats.total += 1
        stats.pending += 1
        stats.last_submission = timezone.now()
        stats.save()

        confirm = self.config.success_message.replace('{tracking_id}', suggestion.short_tracking_id)
        self.api.send_message(chat_id, confirm)

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

        callback = update.get('callback', {})
        callback_id = callback.get('callback_id', '')
        payload = callback.get('payload', '')
        user = callback.get('user', {})
        message_id = callback.get('message', {}).get('mid', '')

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
            if message_id:
                self.api.edit_message(
                    message_id,
                    f'✅ Заявка #{suggestion.short_tracking_id} одобрена.\nМодератор: {mod_name}'
                )

        elif payload.startswith('reject|'):
            uuid_str = payload[7:]
            # Показать кнопки с причинами
            buttons = [
                [{'type': 'callback', 'text': f'{i+1}. {r}', 'payload': f'rr|{uuid_str}|{i}'}]
                for i, r in enumerate(REJECT_REASONS)
            ]
            self.api.answer_callback(callback_id)
            if message_id:
                self.api.edit_message(message_id, 'Выберите причину отклонения:', buttons=buttons)

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
            if message_id:
                self.api.edit_message(
                    message_id,
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

        self.api.send_message(chat_id, '\n'.join(lines))


def process_max_webhook(bot_config, update_data: dict):
    """
    Обработать одно обновление, пришедшее через Django webhook view.
    Используется вместо Long Polling в production.
    """
    bot = MAXSuggestionBot(bot_config)
    update_type = update_data.get('update_type', '')

    if update_type == 'bot_started':
        bot._handle_start(update_data)
    elif update_type == 'message_created':
        bot._handle_message(update_data)
    elif update_type == 'message_callback':
        bot._handle_callback(update_data)
