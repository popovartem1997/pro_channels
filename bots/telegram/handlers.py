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
from telegram.error import BadRequest
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
    await _send_menu(update, context, text=bot_config.welcome_message)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню."""
    await _send_menu(update, context, text='Выберите действие:')


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('📰 Прислать новость', callback_data='menu_send'),
            InlineKeyboardButton('💬 Связаться с админом', callback_data='menu_contact'),
        ],
        [
            InlineKeyboardButton('📬 Мои новости', callback_data='menu_my'),
            InlineKeyboardButton('📊 Статистика', callback_data='menu_stats'),
        ],
    ])


async def _send_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, *, text: str):
    keyboard = _menu_keyboard()
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, reply_markup=keyboard)
    elif update.effective_chat:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=keyboard)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Глобальный обработчик ошибок PTB.
    Без него ошибки в callback/message handlers легко выглядят как "бот молчит".
    """
    try:
        logger.exception("Telegram bot handler error: %s", getattr(context, "error", None))
    except Exception:
        pass


async def _safe_edit_or_reply(query, *, text: str, reply_markup=None, parse_mode=None):
    """
    Иногда Telegram возвращает 400 'Message is not modified' если пытаться
    edit_message_text тем же содержимым. Тогда просто отправляем новое сообщение.
    """
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if 'Message is not modified' in str(e):
            try:
                await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                pass
        else:
            raise


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
    try:
        bot_config = context.bot_data['bot_config']
        user = update.effective_user
        message = update.effective_message

        if not message:
            return

        try:
            chat_id_dbg = int(update.effective_chat.id) if update.effective_chat else 0
        except Exception:
            chat_id_dbg = 0
        try:
            logger.info(
                "[TG] handle_suggestion: bot_id=%s chat_id=%s user_id=%s has_text=%s has_photo=%s has_video=%s has_doc=%s",
                getattr(bot_config, "id", None),
                chat_id_dbg,
                getattr(user, "id", None),
                bool(getattr(message, "text", None)),
                bool(getattr(message, "photo", None)),
                bool(getattr(message, "video", None)),
                bool(getattr(message, "document", None)),
            )
        except Exception:
            pass

        try:
            # Если это чат модерации — это не "предложение", а ответы/действия менеджера.
            chat_id = int(update.effective_chat.id) if update.effective_chat else 0
        except Exception:
            chat_id = 0

        # ВАЖНО: admin_chat_id часто ставят как личный чат владельца с ботом.
        # Если считать личку "чатом модерации", владелец никогда не сможет отправить новость
        # (сообщения будут игнорироваться). Поэтому режим "чат модерации" включаем
        # только для групп/каналов.
        chat_type = getattr(getattr(update, "effective_chat", None), "type", "") or ""
        is_private = (chat_type == "private")
        if chat_id and _is_admin_chat(context, chat_id) and not is_private:
            # Reply-to: менеджер отвечает на пересланную заявку -> отправляем пользователю
            if message.text and getattr(message, 'reply_to_message', None):
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
                            raw_data=message.to_dict(),
                        )
                    await save_outgoing()

                    await message.reply_text('✅ Отправлено пользователю.')
                    return

            # В чате модерации, если не reply — игнорируем, чтобы не плодить "предложения"
            return

        # Не считаем команды предложениями
        if message.text and str(message.text).strip().startswith('/'):
            return

        # Contact flow: user wants to talk to manager (MVP: text only)
        if context.user_data.get('contact_mode'):
            if not message.text:
                await message.reply_text('Пожалуйста, отправьте текстовое сообщение для менеджера.')
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

            await message.reply_text('Сообщение отправлено менеджеру. Мы ответим здесь в чате.')

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

        # Explicit "send news" mode (optional). If user clicked menu_send we ask to send content;
        # here we just reset mode after first accepted message.
        send_mode = bool(context.user_data.get('send_mode'))
        if send_mode:
            context.user_data['send_mode'] = False

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
            await message.reply_text('Извините, этот тип контента не поддерживается.')
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

        try:
            suggestion = await save_suggestion()
        except Exception as e:
            logger.exception('Ошибка сохранения предложения: %s', e)
            await message.reply_text('Не удалось принять новость. Попробуйте ещё раз через минуту.')
            return

        # Подтверждение пользователю
        confirm = bot_config.success_message.replace('{tracking_id}', suggestion.short_tracking_id)
        tracking_tag = f'#{suggestion.short_tracking_id}'
        if tracking_tag not in confirm:
            confirm = f'{tracking_tag}\n' + confirm
        if send_mode:
            confirm = '✅ Новость получена!\n\n' + confirm
        try:
            logger.info("[TG] replying confirm: chat_id=%s tracking=%s", chat_id_dbg, suggestion.short_tracking_id)
        except Exception:
            pass
        await message.reply_text(confirm)
        try:
            logger.info("[TG] replied confirm OK: chat_id=%s tracking=%s", chat_id_dbg, suggestion.short_tracking_id)
        except Exception:
            pass

        # Пересылаем в чат модерации (если настроен)
        admin_chat_ids = []
        try:
            admin_chat_ids = bot_config.get_moderation_chat_ids()
        except Exception:
            admin_chat_ids = [bot_config.admin_chat_id] if bot_config.admin_chat_id else []

        for admin_chat_id in admin_chat_ids:
            await _forward_to_admin(update, context, suggestion, admin_chat_id)
        try:
            await _send_menu(update, context, text='Готово. Хотите сделать что-то ещё?')
        except Exception:
            pass

    except Exception as e:
        logger.exception('handle_suggestion failed: %s', e)
        try:
            if update and update.effective_chat:
                await context.bot.send_message(chat_id=update.effective_chat.id, text='Произошла ошибка. Попробуйте ещё раз.')
        except Exception:
            pass
        return


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

    # ── User menu actions (private chat) ─────────────────────────────────────
    if data in ('menu', 'menu_send', 'menu_contact', 'menu_my', 'menu_stats'):
        if data == 'menu':
            # Не редактируем старое сообщение — присылаем новое
            await query.message.reply_text('Меню:', reply_markup=_menu_keyboard())
            return
        if data == 'menu_send':
            context.user_data['send_mode'] = True
            await query.message.reply_text(
                'Отправьте новость одним сообщением (текст/фото/видео/файл).',
                reply_markup=_menu_keyboard(),
            )
            return
        if data == 'menu_contact':
            bot_config = context.bot_data['bot_config']
            channel = getattr(bot_config, 'channel', None)
            owner = getattr(bot_config, 'owner', None)

            site_nick = ''
            tg_nick = ''
            vk_nick = ''
            max_phone = ''
            try:
                if channel:
                    site_nick = (channel.admin_contact_site or '').strip()
                    tg_nick = (channel.admin_contact_tg or '').strip()
                    vk_nick = (channel.admin_contact_vk or '').strip()
                    max_phone = (channel.admin_contact_max_phone or '').strip()
            except Exception:
                pass
            if not site_nick:
                site_nick = getattr(owner, 'username', '') or ''

            if tg_nick and not tg_nick.startswith('@') and 't.me/' not in tg_nick:
                tg_nick = '@' + tg_nick

            lines = ['Контакты админа канала:']
            if site_nick:
                lines.append(f'— Сайт: {site_nick}')
            if tg_nick:
                lines.append(f'— Telegram: {tg_nick}')
            if vk_nick:
                lines.append(f'— VK: {vk_nick}')
            if max_phone:
                lines.append(f'— MAX (телефон): {max_phone}')
            if len(lines) == 1:
                lines.append('— Контакты не заполнены. Админ может добавить их в настройках канала.')

            # Log on site
            try:
                from asgiref.sync import sync_to_async

                @sync_to_async
                def log_press():
                    from bots.models import AuditLog
                    AuditLog.objects.create(
                        actor=None,
                        owner=owner,
                        action='bot.contact_pressed',
                        object_type='SuggestionBot',
                        object_id=str(getattr(bot_config, 'id', '')),
                        data={
                            'channel_id': getattr(channel, 'id', None),
                            'platform': 'telegram',
                            'platform_user_id': str(update.effective_user.id) if update and update.effective_user else '',
                            'platform_username': getattr(update.effective_user, 'username', '') if update and update.effective_user else '',
                        },
                    )
                await log_press()
            except Exception:
                pass

            context.user_data['contact_mode'] = True
            await query.message.reply_text('\n'.join(lines))
            await query.message.reply_text(
                'Напишите сообщение админу одним сообщением (текст).',
                reply_markup=_menu_keyboard(),
            )
            return
        if data == 'menu_my':
            await _send_my_news(update, context)
            return
        if data == 'menu_stats':
            await _send_my_stats(update, context)
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


async def _send_my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_config = context.bot_data['bot_config']
    user = update.effective_user

    @sync_to_async
    def get_stats():
        from bots.models import SuggestionUserStats
        return SuggestionUserStats.objects.filter(bot=bot_config, platform_user_id=str(user.id)).first()

    st = await get_stats()
    if not st:
        text = 'Пока нет статистики. Нажмите «Прислать новость» и отправьте сообщение.'
    else:
        text = (
            '📊 Ваша статистика\n\n'
            f'📬 Всего: {st.total}\n'
            f'⏳ На модерации: {st.pending}\n'
            f'✅ Одобрено: {st.approved}\n'
            f'❌ Отклонено: {st.rejected}\n'
            f'📢 Опубликовано: {st.published}\n'
        )
    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, reply_markup=_menu_keyboard())
    elif update.message:
        await update.message.reply_text(text, reply_markup=_menu_keyboard())


async def _send_my_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_config = context.bot_data['bot_config']
    user = update.effective_user

    @sync_to_async
    def get_recent():
        from bots.models import Suggestion
        return list(
            Suggestion.objects.filter(bot=bot_config, platform_user_id=str(user.id))
            .order_by('-submitted_at')[:10]
        )

    items = await get_recent()
    if not items:
        text = 'У вас пока нет отправленных новостей. Нажмите «Прислать новость».'
    else:
        lines = ['📬 Ваши новости (последние 10):\n']
        for s in items:
            lines.append(f'{s.status_emoji} #{s.short_tracking_id} — {s.get_status_display()}')
        lines.append('\nКоманда: /status — подробная статистика.')
        text = '\n'.join(lines)
    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, reply_markup=_menu_keyboard())
    elif update.message:
        await update.message.reply_text(text, reply_markup=_menu_keyboard())


async def _process_approve(query, bot_config, uuid_str: str):
    """Одобрить заявку, уведомить пользователя."""
    suggestion = await _get_suggestion(bot_config, uuid_str)
    if not suggestion:
        await query.message.reply_text('Заявка не найдена.')
        return

    if suggestion.status != 'pending':
        await query.message.reply_text(f'Заявка уже обработана: {suggestion.get_status_display()}')
        return

    @sync_to_async
    def do_approve():
        suggestion.approve()

    await do_approve()

    # Уведомление пользователю
    notify = bot_config.approved_message.replace('{tracking_id}', suggestion.short_tracking_id)
    await _notify_user(query.bot, suggestion.platform_user_id, notify)

    # Ссылка на создание поста на сайте (черновик из предложки)
    try:
        from django.conf import settings
        from django.urls import reverse
        url = f"{settings.SITE_URL}{reverse('content:create_from_suggestion', kwargs={'tracking_id': suggestion.tracking_id})}"
        await query.message.reply_text(
            '📝 Открыть создание поста из этой новости (текст + медиа будут подставлены):',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton('📝 Создать пост', url=url),
            ]]),
        )
    except Exception:
        pass

    moderator = query.from_user.first_name or 'Модератор'
    await query.message.reply_text(
        (
            f'✅ Заявка `#{suggestion.short_tracking_id}` одобрена.\n'
            f'Модератор: {moderator}'
        ),
        parse_mode='Markdown',
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
        await query.message.reply_text('Заявка не найдена.')
        return

    if suggestion.status != 'pending':
        await query.message.reply_text(f'Заявка уже обработана: {suggestion.get_status_display()}')
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
    await query.message.reply_text(
        (
            f'❌ Заявка `#{suggestion.short_tracking_id}` отклонена.\n'
            f'Причина: {reason}\n'
            f'Модератор: {moderator}'
        ),
        parse_mode='Markdown',
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
    app.add_handler(CommandHandler('menu', cmd_menu))
    app.add_handler(CommandHandler('status', cmd_status))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(_on_error)
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
