"""
Обработчики для Telegram бота-предложки.

Схема работы:
  1. Пользователь пишет /start — получает приветствие
  2. Отправляет любой контент — создаётся Suggestion, пользователю приходит tracking_id
  3. Заявка пересылается в admin_chat_id с кнопками ✅ / ❌
  4. Модератор нажимает кнопку — пользователь получает уведомление о статусе
  5. /status — пользователь видит свою статистику и последние заявки

Callback data формат (не более 64 байт — ограничение Telegram):
  approve|<uuid>          — одобрить
  reject|<uuid>           — выбрать причину отклонения
  rr|<uuid>|<idx>         — подтвердить отклонение с причиной (rr = reject_reason)
"""
import logging
import uuid as uuid_module

from asgiref.sync import sync_to_async
from django.utils import timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

logger = logging.getLogger(__name__)

# Варианты причин отклонения (индекс → текст)
REJECT_REASONS = [
    'Не по теме',
    'Спам или реклама',
    'Низкое качество',
    'Нарушение правил',
]


# ─── Команды ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие при /start."""
    bot_config = context.bot_data['bot_config']
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('💬 Связаться с менеджером', callback_data='contact'),
    ]])
    if update.message:
        await update.message.reply_text(bot_config.welcome_message, reply_markup=keyboard)
    elif update.effective_chat:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=bot_config.welcome_message, reply_markup=keyboard)


def _is_admin_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    ids = context.bot_data.get('admin_chat_ids') or []
    try:
        return str(chat_id) in [str(x) for x in ids]
    except Exception:
        return False


def _extract_user_id_from_admin_caption(text: str) -> str | None:
    # caption содержит строку: 🆔 `123456`
    import re
    if not text:
        return None
    m = re.search(r'🆔\s*`(\d+)`', text)
    if m:
        return m.group(1)
    return None


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /status — показать пользователю его статистику и последние 5 заявок.
    """
    bot_config = context.bot_data['bot_config']
    user = update.effective_user

    @sync_to_async
    def get_data():
        from bots.models import SuggestionUserStats, Suggestion
        stats = SuggestionUserStats.objects.filter(
            bot=bot_config, platform_user_id=str(user.id)
        ).first()
        recent = list(
            Suggestion.objects.filter(
                bot=bot_config, platform_user_id=str(user.id)
            ).order_by('-submitted_at')[:5]
        )
        return stats, recent

    stats, recent = await get_data()

    if not stats or stats.total == 0:
        await update.message.reply_text('Вы ещё не отправляли предложений.')
        return

    lines = [
        '📊 *Ваша статистика:*\n',
        f'📬 Всего: {stats.total}',
        f'✅ Одобрено: {stats.approved}',
        f'❌ Отклонено: {stats.rejected}',
        f'⏳ На модерации: {stats.pending}',
        f'📢 Опубликовано: {stats.published}',
        '',
        '*Последние заявки:*',
    ]
    for s in recent:
        lines.append(f'{s.status_emoji} `#{s.short_tracking_id}` — {s.get_status_display()}')

    await update.message.reply_text(
        '\n'.join(lines),
        parse_mode='Markdown'
    )


# ─── Приём предложения ────────────────────────────────────────────────────────

async def handle_suggestion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Любое сообщение (текст, фото, видео, документ, аудио, голос) — новое предложение.
    """
    bot_config = context.bot_data['bot_config']
    user = update.effective_user
    message = update.effective_message

    # Если это чат модерации — это не "предложение", а ответы/действия менеджера.
    try:
        chat_id = int(update.effective_chat.id) if update.effective_chat else 0
    except Exception:
        chat_id = 0

    if chat_id and _is_admin_chat(context, chat_id):
        # Reply-to: менеджер отвечает на пересланную заявку -> отправляем пользователю
        if message and message.text and getattr(message, 'reply_to_message', None):
            replied = message.reply_to_message
            replied_text = (getattr(replied, 'text', None) or getattr(replied, 'caption', None) or '') or ''
            user_id = _extract_user_id_from_admin_caption(replied_text)
            if user_id:
                try:
                    await context.bot.send_message(chat_id=user_id, text=message.text)
                except Exception as e:
                    await message.reply_text(f'Не удалось отправить пользователю: {e}')
                    return

                @sync_to_async
                def save_outgoing():
                    from bots.models import BotConversation, BotConversationMessage
                    conv, _ = BotConversation.objects.get_or_create(
                        bot=bot_config,
                        platform_user_id=str(user_id),
                        defaults={'last_message_at': timezone.now()},
                    )
                    conv.last_message_at = timezone.now()
                    conv.status = 'open'
                    conv.save(update_fields=['last_message_at', 'status'])
                    BotConversationMessage.objects.create(
                        conversation=conv,
                        direction='out',
                        sender_user=None,
                        text=message.text,
                        raw_data=message.to_dict() if message else {},
                    )
                await save_outgoing()

                await message.reply_text('✅ Отправлено пользователю.')
                return

        # В чате модерации, если не reply — игнорируем, чтобы не плодить "предложения"
        return

    # Contact flow: user wants to talk to manager (MVP: text only)
    if context.user_data.get('contact_mode'):
        if not message.text:
            await update.message.reply_text('Пожалуйста, отправьте текстовое сообщение для менеджера.')
            return

        @sync_to_async
        def save_dialog():
            from bots.models import BotConversation, BotConversationMessage
            conv, _ = BotConversation.objects.get_or_create(
                bot=bot_config,
                platform_user_id=str(user.id),
                defaults={
                    'platform_username': user.username or '',
                    'display_name': _build_display_name(user),
                    'last_message_at': timezone.now(),
                }
            )
            conv.platform_username = user.username or conv.platform_username
            conv.display_name = _build_display_name(user)
            conv.last_message_at = timezone.now()
            conv.status = 'open'
            conv.save(update_fields=['platform_username', 'display_name', 'last_message_at', 'status'])
            BotConversationMessage.objects.create(
                conversation=conv,
                direction='in',
                text=message.text or '',
                raw_data=message.to_dict(),
            )
            return conv.pk

        conv_id = await save_dialog()
        context.user_data['contact_mode'] = False

        await update.message.reply_text('Сообщение отправлено менеджеру. Мы ответим здесь в чате.')

        # Notify moderation chats with link to site dialog
        admin_chat_ids = []
        try:
            admin_chat_ids = bot_config.get_moderation_chat_ids()
        except Exception:
            admin_chat_ids = [bot_config.admin_chat_id] if bot_config.admin_chat_id else []
        try:
            from django.conf import settings
            url = f'{settings.SITE_URL}/bots/conversations/{conv_id}/'
        except Exception:
            url = ''
        if admin_chat_ids:
            text_notify = f'💬 Новое сообщение для менеджера от @{user.username or user.id}\nДиалог: {url}'.strip()
            for admin_chat_id in admin_chat_ids:
                try:
                    await context.bot.send_message(chat_id=admin_chat_id, text=text_notify)
                except Exception:
                    pass
        return

    # Определяем тип контента и собираем медиа-ID
    from bots.models import Suggestion

    if message.photo:
        content_type = Suggestion.CONTENT_PHOTO
        media_ids = [message.photo[-1].file_id]  # берём максимальное разрешение
        text = message.caption or ''
    elif message.video:
        content_type = Suggestion.CONTENT_VIDEO
        media_ids = [message.video.file_id]
        text = message.caption or ''
    elif message.document:
        content_type = Suggestion.CONTENT_DOCUMENT
        media_ids = [message.document.file_id]
        text = message.caption or ''
    elif message.audio:
        content_type = Suggestion.CONTENT_AUDIO
        media_ids = [message.audio.file_id]
        text = message.caption or ''
    elif message.voice:
        content_type = Suggestion.CONTENT_VOICE
        media_ids = [message.voice.file_id]
        text = ''
    elif message.text:
        content_type = Suggestion.CONTENT_TEXT
        media_ids = []
        text = message.text
    else:
        await update.message.reply_text('Извините, этот тип контента не поддерживается.')
        return

    @sync_to_async
    def save_suggestion():
        from bots.models import SuggestionUserStats
        suggestion = Suggestion.objects.create(
            bot=bot_config,
            platform_user_id=str(user.id),
            platform_username=user.username or '',
            platform_first_name=user.first_name or '',
            platform_last_name=user.last_name or '',
            content_type=content_type,
            text=text,
            media_file_ids=media_ids,
            raw_data=message.to_dict(),
        )
        # Создаём/обновляем статистику пользователя
        stats, created = SuggestionUserStats.objects.get_or_create(
            bot=bot_config,
            platform_user_id=str(user.id),
            defaults={
                'platform_username': user.username or '',
                'display_name': _build_display_name(user),
            }
        )
        if not created:
            if user.username:
                stats.platform_username = user.username
            stats.display_name = _build_display_name(user)
        stats.total += 1
        stats.pending += 1
        stats.last_submission = timezone.now()
        stats.save()
        return suggestion

    suggestion = await save_suggestion()

    # Подтверждение пользователю
    confirm = bot_config.success_message.replace('{tracking_id}', suggestion.short_tracking_id)
    await update.message.reply_text(confirm)

    # Пересылаем в чат модерации (если настроен)
    admin_chat_ids = []
    try:
        admin_chat_ids = bot_config.get_moderation_chat_ids()
    except Exception:
        admin_chat_ids = [bot_config.admin_chat_id] if bot_config.admin_chat_id else []

    for admin_chat_id in admin_chat_ids:
        await _forward_to_admin(update, context, suggestion, admin_chat_id)


def _build_display_name(user) -> str:
    name = ' '.join(filter(None, [user.first_name or '', user.last_name or ''])).strip()
    return name or user.username or str(user.id)


async def _forward_to_admin(update, context, suggestion, admin_chat_id: str):
    """Переслать заявку в чат модерации с кнопками одобрить/отклонить."""
    user = update.effective_user
    message = update.effective_message

    sender = f'{user.first_name or ""} {user.last_name or ""}'.strip()
    if user.username:
        sender += f' (@{user.username})'

    caption = (
        f'📬 Новое предложение `#{suggestion.short_tracking_id}`\n\n'
        f'👤 {sender}\n'
        f'🆔 `{user.id}`\n'
        f'📎 Тип: {suggestion.get_content_type_display()}'
    )
    if suggestion.text:
        preview = suggestion.text[:300]
        if len(suggestion.text) > 300:
            preview += '…'
        caption += f'\n\n📝 {preview}'

    uuid_str = str(suggestion.tracking_id)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('✅ Одобрить', callback_data=f'approve|{uuid_str}'),
        InlineKeyboardButton('❌ Отклонить', callback_data=f'reject|{uuid_str}'),
    ]])

    try:
        if message.photo:
            await context.bot.send_photo(
                chat_id=admin_chat_id, photo=message.photo[-1].file_id,
                caption=caption, reply_markup=keyboard, parse_mode='Markdown'
            )
        elif message.video:
            await context.bot.send_video(
                chat_id=admin_chat_id, video=message.video.file_id,
                caption=caption, reply_markup=keyboard, parse_mode='Markdown'
            )
        elif message.document:
            await context.bot.send_document(
                chat_id=admin_chat_id, document=message.document.file_id,
                caption=caption, reply_markup=keyboard, parse_mode='Markdown'
            )
        elif message.audio:
            await context.bot.send_audio(
                chat_id=admin_chat_id, audio=message.audio.file_id,
                caption=caption, reply_markup=keyboard, parse_mode='Markdown'
            )
        elif message.voice:
            await context.bot.send_voice(
                chat_id=admin_chat_id, voice=message.voice.file_id,
                caption=caption, reply_markup=keyboard, parse_mode='Markdown'
            )
        else:
            await context.bot.send_message(
                chat_id=admin_chat_id, text=caption,
                reply_markup=keyboard, parse_mode='Markdown'
            )
    except Exception as e:
        logger.error('Ошибка при пересылке в чат модерации %s: %s', admin_chat_id, e)


# ─── Callback-обработчик кнопок модерации ─────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает нажатия кнопок ✅ и ❌ в чате модерации.
    """
    query = update.callback_query
    await query.answer()

    bot_config = context.bot_data['bot_config']
    data = query.data

    if data == 'contact':
        context.user_data['contact_mode'] = True
        await query.edit_message_text('Напишите текст сообщения для менеджера одним сообщением.')
        return

    if data.startswith('approve|'):
        await _process_approve(query, bot_config, data[8:])

    elif data.startswith('reject|'):
        await _show_reject_reasons(query, data[7:])

    elif data.startswith('rr|'):
        # rr|<uuid>|<reason_index>
        parts = data.split('|', 2)
        if len(parts) == 3:
            await _process_reject(query, bot_config, parts[1], int(parts[2]))


async def _process_approve(query, bot_config, uuid_str: str):
    """Одобрить заявку, уведомить пользователя."""
    suggestion = await _get_suggestion(bot_config, uuid_str)
    if not suggestion:
        await query.edit_message_text('Заявка не найдена.')
        return

    if suggestion.status != 'pending':
        await query.edit_message_text(
            f'Заявка уже обработана: {suggestion.get_status_display()}'
        )
        return

    @sync_to_async
    def do_approve():
        suggestion.approve()

    await do_approve()

    # Уведомление пользователю
    notify = bot_config.approved_message.replace('{tracking_id}', suggestion.short_tracking_id)
    await _notify_user(query.bot, suggestion.platform_user_id, notify)

    moderator = query.from_user.first_name or 'Модератор'
    await query.edit_message_text(
        f'✅ Заявка `#{suggestion.short_tracking_id}` одобрена.\n'
        f'Модератор: {moderator}',
        parse_mode='Markdown'
    )


async def _show_reject_reasons(query, uuid_str: str):
    """Показать кнопки с причинами отклонения."""
    buttons = [
        [InlineKeyboardButton(reason, callback_data=f'rr|{uuid_str}|{idx}')]
        for idx, reason in enumerate(REJECT_REASONS)
    ]
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def _process_reject(query, bot_config, uuid_str: str, reason_idx: int):
    """Отклонить заявку с выбранной причиной."""
    suggestion = await _get_suggestion(bot_config, uuid_str)
    if not suggestion:
        await query.edit_message_text('Заявка не найдена.')
        return

    if suggestion.status != 'pending':
        await query.edit_message_text(
            f'Заявка уже обработана: {suggestion.get_status_display()}'
        )
        return

    reason = REJECT_REASONS[reason_idx] if 0 <= reason_idx < len(REJECT_REASONS) else 'Не соответствует требованиям'

    @sync_to_async
    def do_reject():
        suggestion.reject(reason=reason)

    await do_reject()

    notify = (
        bot_config.rejected_message
        .replace('{tracking_id}', suggestion.short_tracking_id)
        .replace('{reason}', reason)
    )
    await _notify_user(query.bot, suggestion.platform_user_id, notify)

    moderator = query.from_user.first_name or 'Модератор'
    await query.edit_message_text(
        f'❌ Заявка `#{suggestion.short_tracking_id}` отклонена.\n'
        f'Причина: {reason}\n'
        f'Модератор: {moderator}',
        parse_mode='Markdown'
    )


# ─── Вспомогательные функции ──────────────────────────────────────────────────

@sync_to_async
def _get_suggestion(bot_config, uuid_str: str):
    """Получить заявку из БД по UUID строке."""
    from bots.models import Suggestion
    try:
        uid = uuid_module.UUID(uuid_str)
        return Suggestion.objects.get(tracking_id=uid, bot=bot_config)
    except (Suggestion.DoesNotExist, ValueError):
        return None


async def _notify_user(bot, user_id: str, text: str):
    """Отправить уведомление пользователю (молча, если не получилось)."""
    try:
        await bot.send_message(chat_id=user_id, text=text)
    except Exception as e:
        logger.warning('Не удалось отправить уведомление пользователю %s: %s', user_id, e)


# ─── Сборка Application ───────────────────────────────────────────────────────

def build_application(bot_config) -> Application:
    """
    Создать и настроить Application для конкретного бота.
    bot_config — объект SuggestionBot из БД.
    """
    token = bot_config.get_token()
    app = Application.builder().token(token).build()

    # Сохраняем конфиг в bot_data — доступен во всех хендлерах
    app.bot_data['bot_config'] = bot_config
    # Кешируем чаты модерации (для распознавания "ответа менеджера")
    try:
        app.bot_data['admin_chat_ids'] = bot_config.get_moderation_chat_ids()
    except Exception:
        app.bot_data['admin_chat_ids'] = [bot_config.admin_chat_id] if bot_config.admin_chat_id else []

    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('status', cmd_status))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(
        filters.TEXT
        | filters.PHOTO
        | filters.VIDEO
        | filters.Document.ALL
        | filters.AUDIO
        | filters.VOICE,
        handle_suggestion
    ))

    return app
