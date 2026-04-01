"""
VK бот-предложка через VK Community Messages API.

Схема работы:
  1. Long Polling: бот слушает новые сообщения в сообществе
  2. Пользователь пишет в сообщество — создаётся Suggestion
  3. Заявка пересылается в беседу/чат модераторов (group_id = ID беседы)
  4. Модератор отвечает командой !одобрить <ID> или !отклонить <ID> <причина>
  5. Пользователь получает уведомление

Для работы нужен:
  - Токен сообщества (manage + messages + photos)
  - Long Poll API включён в настройках сообщества
"""
import logging
import time
import os

import django
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from django.utils import timezone

logger = logging.getLogger(__name__)

# Причины отклонения — модератор пишет номер
REJECT_REASONS = [
    'Не по теме',
    'Спам или реклама',
    'Низкое качество',
    'Нарушение правил',
]


class VKSuggestionBot:
    """
    Бот предложки для VK-сообщества.
    Запускается через management-команду run_vk_bots.
    """

    def __init__(self, bot_config):
        """
        bot_config — объект SuggestionBot из БД.
        """
        self.config = bot_config
        token = bot_config.get_token()
        self.vk_session = vk_api.VkApi(token=token)
        self.vk = self.vk_session.get_api()
        self.upload = vk_api.VkUpload(self.vk_session)

    def run(self):
        """Запустить Long Polling цикл."""
        logger.info('[VK] Запуск бота "%s"', self.config.name)
        longpoll = VkLongPoll(self.vk_session)

        try:
            for event in longpoll.listen():
                if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                    self._handle_message(event)
        except KeyboardInterrupt:
            logger.info('[VK] Бот остановлен.')
        except Exception as e:
            logger.exception('[VK] Ошибка в цикле Long Polling: %s', e)
            # Небольшая пауза перед перезапуском
            time.sleep(5)
            self.run()

    def _handle_message(self, event):
        """Обработать входящее сообщение."""
        from bots.models import Suggestion, SuggestionUserStats

        user_id = str(event.user_id)
        text = event.text or ''

        # Команды модератора из беседы модерации (если это беседа, не личка)
        if hasattr(event, 'chat_id') and event.chat_id:
            self._handle_moderator_command(text, event)
            return

        # Приветствие
        if text.lower() in ('/start', 'начать', 'привет', 'start'):
            self._send(user_id, self.config.welcome_message)
            return

        # Статус заявок
        if text.lower() in ('/status', 'статус', 'мои заявки'):
            self._send_status(user_id)
            return

        # Определяем тип контента
        content_type, media_ids = self._detect_content(event)
        if not content_type:
            self._send(user_id, 'Извините, этот тип контента не поддерживается.')
            return

        # Получаем информацию о пользователе
        user_info = self._get_user_info(user_id)

        # Сохраняем заявку
        suggestion = Suggestion.objects.create(
            bot=self.config,
            platform_user_id=user_id,
            platform_username=user_info.get('screen_name', ''),
            platform_first_name=user_info.get('first_name', ''),
            platform_last_name=user_info.get('last_name', ''),
            content_type=content_type,
            text=text,
            media_file_ids=media_ids,
            raw_data={
                'user_id': event.user_id,
                'message_id': event.message_id,
                'text': text,
                'attachments': str(getattr(event, 'attachments', {})),
            },
        )

        # Обновляем статистику
        stats, created = SuggestionUserStats.objects.get_or_create(
            bot=self.config,
            platform_user_id=user_id,
            defaults={
                'platform_username': user_info.get('screen_name', ''),
                'display_name': f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip(),
            }
        )
        stats.total += 1
        stats.pending += 1
        stats.last_submission = timezone.now()
        stats.save()

        # Подтверждение пользователю
        confirm = self.config.success_message.replace('{tracking_id}', suggestion.short_tracking_id)
        self._send(user_id, confirm)

        # Переслать модераторам
        if self.config.group_id:
            self._forward_to_moderators(suggestion, user_info, text, media_ids)

    def _detect_content(self, event):
        """Вернуть (content_type, media_ids) из события."""
        from bots.models import Suggestion

        attachments = getattr(event, 'attachments', {})
        text = event.text or ''
        media_ids = []
        content_type = None

        if attachments:
            for key, value in attachments.items():
                if key.startswith('attach') and not key.endswith('_type'):
                    media_ids.append(value)
            attach_types = [v for k, v in attachments.items() if k.endswith('_type')]
            if 'photo' in attach_types:
                content_type = Suggestion.CONTENT_PHOTO
            elif 'video' in attach_types:
                content_type = Suggestion.CONTENT_VIDEO
            elif 'audio' in attach_types:
                content_type = Suggestion.CONTENT_AUDIO
            elif 'doc' in attach_types:
                content_type = Suggestion.CONTENT_DOCUMENT
            else:
                content_type = Suggestion.CONTENT_MIXED
        elif text:
            content_type = Suggestion.CONTENT_TEXT

        return content_type, media_ids

    def _get_user_info(self, user_id: str) -> dict:
        """Получить имя и screen_name пользователя."""
        try:
            result = self.vk.users.get(
                user_ids=user_id,
                fields='screen_name'
            )
            return result[0] if result else {}
        except Exception as e:
            logger.warning('[VK] Не удалось получить данные пользователя %s: %s', user_id, e)
            return {}

    def _forward_to_moderators(self, suggestion, user_info: dict, text: str, media_ids: list):
        """Переслать заявку в беседу модераторов."""
        first = user_info.get('first_name', '')
        last = user_info.get('last_name', '')
        screen = user_info.get('screen_name', '')
        sender = f'{first} {last}'.strip()
        if screen:
            sender += f' (@{screen})'

        message = (
            f'📬 Новое предложение #{suggestion.short_tracking_id}\n\n'
            f'👤 {sender}\n'
            f'🆔 {suggestion.platform_user_id}\n'
            f'📎 Тип: {suggestion.get_content_type_display()}'
        )
        if text:
            preview = text[:300] + ('…' if len(text) > 300 else '')
            message += f'\n\n📝 {preview}'

        message += (
            f'\n\n─────────────────\n'
            f'✅ !одобрить {suggestion.short_tracking_id}\n'
            f'❌ !отклонить {suggestion.short_tracking_id} <причина>\n'
            f'   Причины: {", ".join(f"{i+1}-{r}" for i, r in enumerate(REJECT_REASONS))}'
        )

        try:
            # group_id в данном случае — это ID беседы (peer_id = 2000000000 + chat_id)
            # или ID группы для сообщений через API v5.90+
            chat_peer_id = int(self.config.group_id)
            self.vk.messages.send(
                peer_id=chat_peer_id,
                message=message,
                random_id=int(time.time() * 1000),
            )
        except Exception as e:
            logger.error('[VK] Ошибка пересылки модераторам: %s', e)

    def _handle_moderator_command(self, text: str, event):
        """Обработка команд модератора из беседы."""
        from bots.models import Suggestion

        text_lower = text.lower().strip()

        if text_lower.startswith('!одобрить ') or text_lower.startswith('!approve '):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                return
            short_id = parts[1].strip().upper()
            suggestion = self._find_suggestion(short_id)
            if not suggestion:
                return
            if suggestion.status != Suggestion.STATUS_PENDING:
                return
            suggestion.approve()
            from bots.services import format_approved_subscriber_message

            notify = format_approved_subscriber_message(
                self.config.approved_message or '',
                suggestion.short_tracking_id,
            )
            self._send(suggestion.platform_user_id, notify)
            self.vk.messages.send(
                peer_id=event.peer_id,
                message=f'✅ Заявка #{short_id} одобрена.',
                random_id=int(time.time() * 1000),
            )

        elif text_lower.startswith('!отклонить ') or text_lower.startswith('!reject '):
            parts = text.split(maxsplit=2)
            short_id = parts[1].strip().upper() if len(parts) > 1 else ''
            reason = parts[2].strip() if len(parts) > 2 else 'Не соответствует требованиям'
            # Если reason — цифра, берём из списка
            if reason.isdigit():
                idx = int(reason) - 1
                reason = REJECT_REASONS[idx] if 0 <= idx < len(REJECT_REASONS) else reason
            suggestion = self._find_suggestion(short_id)
            if not suggestion:
                return
            if suggestion.status != Suggestion.STATUS_PENDING:
                return
            suggestion.reject(reason=reason)
            from bots.services import format_rejected_subscriber_message

            notify = format_rejected_subscriber_message(
                self.config.rejected_message or '',
                suggestion.short_tracking_id,
                reason,
            )
            self._send(suggestion.platform_user_id, notify)
            self.vk.messages.send(
                peer_id=event.peer_id,
                message=f'❌ Заявка #{short_id} отклонена. Причина: {reason}',
                random_id=int(time.time() * 1000),
            )

    def _find_suggestion(self, short_id: str):
        """Найти заявку по первым 8 символам tracking_id."""
        from bots.models import Suggestion
        short_id = short_id.upper()
        return Suggestion.objects.filter(
            bot=self.config,
            tracking_id__startswith=short_id.lower()
        ).first()

    def _send_status(self, user_id: str):
        """Отправить статистику пользователю."""
        from bots.models import SuggestionUserStats, Suggestion

        stats = SuggestionUserStats.objects.filter(
            bot=self.config, platform_user_id=user_id
        ).first()
        if not stats or stats.total == 0:
            self._send(user_id, 'Вы ещё не отправляли предложений.')
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

        self._send(user_id, '\n'.join(lines))

    def _send(self, user_id: str, text: str):
        """Отправить сообщение пользователю."""
        try:
            self.vk.messages.send(
                user_id=int(user_id),
                message=text,
                random_id=int(time.time() * 1000),
            )
        except Exception as e:
            logger.warning('[VK] Не удалось отправить сообщение %s: %s', user_id, e)
