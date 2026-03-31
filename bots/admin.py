"""
Django Admin для системы ботов-предложок.

Возможности:
  - Создание ботов (токен вводится открытым текстом → шифруется автоматически)
  - Список предложений с фильтрами по статусу, боту, дате
  - Кнопки "Одобрить" / "Отклонить" прямо в списке
  - Уведомления пользователей при изменении статуса через admin-действия
  - Лидерборд пользователей
"""
from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from django.contrib import messages
from django import forms

from .models import SuggestionBot, Suggestion, SuggestionUserStats


# ─── Форма для создания/редактирования бота ───────────────────────────────────

class SuggestionBotForm(forms.ModelForm):
    """
    Форма с полем raw_token: вводим токен открытым текстом,
    он автоматически шифруется при сохранении.
    """
    raw_token = forms.CharField(
        label='Токен бота',
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text='Введите токен — он будет сохранён зашифрованным. Оставьте пустым, чтобы не менять.'
    )

    class Meta:
        model = SuggestionBot
        exclude = ['bot_token_encrypted']

    def save(self, commit=True):
        instance = super().save(commit=False)
        raw_token = self.cleaned_data.get('raw_token', '').strip()
        if raw_token:
            instance.set_token(raw_token)
        elif not instance.pk:
            # Новый бот без токена — устанавливаем заглушку
            instance.bot_token_encrypted = 'NOT_SET'
        if commit:
            instance.save()
        return instance


# ─── SuggestionBot Admin ──────────────────────────────────────────────────────

@admin.register(SuggestionBot)
class SuggestionBotAdmin(admin.ModelAdmin):
    form = SuggestionBotForm
    list_display = [
        'name', 'platform_badge', 'owner', 'bot_username',
        'is_active', 'suggestions_pending', 'created_at'
    ]
    list_filter = ['platform', 'is_active']
    search_fields = ['name', 'bot_username', 'owner__username']
    readonly_fields = ['created_at', 'updated_at', 'webhook_url_hint']

    fieldsets = (
        ('Основное', {
            'fields': ('owner', 'name', 'platform', 'raw_token', 'bot_username', 'is_active')
        }),
        ('Настройки модерации', {
            'fields': ('admin_chat_id', 'group_id'),
            'description': 'Telegram: ID чата для кнопок. VK/MAX: ID сообщества/беседы.'
        }),
        ('Сообщения бота', {
            'classes': ('collapse',),
            'fields': ('welcome_message', 'success_message', 'approved_message', 'rejected_message')
        }),
        ('Webhook', {
            'classes': ('collapse',),
            'fields': ('webhook_url_hint', 'created_at', 'updated_at')
        }),
    )

    def platform_badge(self, obj):
        colors = {
            'telegram': '#0088cc',
            'vk': '#4a76a8',
            'max': '#ff6600',
        }
        color = colors.get(obj.platform, '#888')
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:3px;">{}</span>',
            color, obj.get_platform_display()
        )
    platform_badge.short_description = 'Платформа'

    def suggestions_pending(self, obj):
        count = obj.suggestions.filter(status=Suggestion.STATUS_PENDING).count()
        if count > 0:
            return format_html('<b style="color:orange;">{} ⏳</b>', count)
        return '0'
    suggestions_pending.short_description = 'На модерации'

    def webhook_url_hint(self, obj):
        if not obj.pk:
            return 'Сохраните бота, чтобы увидеть URL'
        from django.urls import reverse
        platform_views = {
            'telegram': 'bots:telegram_webhook',
            'vk': 'bots:vk_webhook',
            'max': 'bots:max_webhook',
        }
        view_name = platform_views.get(obj.platform, '')
        if view_name:
            try:
                url = reverse(view_name, kwargs={'bot_id': obj.pk})
                return format_html(
                    'Webhook URL: <code>https://ВАШ-ДОМЕН{}</code>',
                    url
                )
            except Exception:
                pass
        return '—'
    webhook_url_hint.short_description = 'Webhook URL'


# ─── Suggestion Admin ─────────────────────────────────────────────────────────

@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    list_display = [
        'short_id', 'status_badge', 'bot', 'sender_display',
        'content_type_badge', 'text_preview', 'submitted_at', 'moderated_by'
    ]
    list_filter = ['status', 'bot', 'content_type', 'submitted_at']
    search_fields = ['platform_username', 'platform_first_name', 'text', 'tracking_id']
    readonly_fields = [
        'tracking_id', 'short_tracking_id', 'bot', 'platform_user_id',
        'platform_username', 'platform_first_name', 'platform_last_name',
        'content_type', 'text', 'media_file_ids', 'raw_data',
        'submitted_at', 'updated_at', 'moderated_at', 'moderated_by',
    ]
    actions = ['action_approve', 'action_reject']

    fieldsets = (
        ('Заявка', {
            'fields': ('tracking_id', 'bot', 'status', 'submitted_at')
        }),
        ('Отправитель', {
            'fields': ('platform_user_id', 'platform_username', 'platform_first_name', 'platform_last_name')
        }),
        ('Контент', {
            'fields': ('content_type', 'text', 'media_file_ids')
        }),
        ('Модерация', {
            'fields': ('moderated_by', 'moderated_at', 'rejection_reason', 'moderator_note')
        }),
        ('Технические данные', {
            'classes': ('collapse',),
            'fields': ('raw_data', 'updated_at')
        }),
    )

    def short_id(self, obj):
        return format_html('<code>#{}</code>', obj.short_tracking_id)
    short_id.short_description = 'ID'

    def status_badge(self, obj):
        colors = {
            Suggestion.STATUS_PENDING: ('#ff9900', '⏳ На модерации'),
            Suggestion.STATUS_APPROVED: ('#28a745', '✅ Одобрено'),
            Suggestion.STATUS_REJECTED: ('#dc3545', '❌ Отклонено'),
            Suggestion.STATUS_PUBLISHED: ('#007bff', '📢 Опубликовано'),
        }
        color, label = colors.get(obj.status, ('#888', obj.get_status_display()))
        return format_html(
            '<span style="color:{};">{}</span>', color, label
        )
    status_badge.short_description = 'Статус'

    def content_type_badge(self, obj):
        icons = {
            'text': '📝', 'photo': '🖼️', 'video': '🎥',
            'document': '📎', 'audio': '🎵', 'voice': '🎤', 'mixed': '🗂️',
        }
        icon = icons.get(obj.content_type, '?')
        return f'{icon} {obj.get_content_type_display()}'
    content_type_badge.short_description = 'Тип'

    def text_preview(self, obj):
        if obj.text:
            return obj.text[:80] + ('…' if len(obj.text) > 80 else '')
        return '(медиа)'
    text_preview.short_description = 'Текст'

    @admin.action(description='✅ Одобрить выбранные заявки')
    def action_approve(self, request, queryset):
        pending = queryset.filter(status=Suggestion.STATUS_PENDING)
        count = 0
        for suggestion in pending:
            suggestion.approve(moderator=request.user)
            self._notify_async(suggestion, 'approved')
            count += 1
        self.message_user(request, f'Одобрено {count} заявок.', messages.SUCCESS)

    @admin.action(description='❌ Отклонить выбранные заявки')
    def action_reject(self, request, queryset):
        pending = queryset.filter(status=Suggestion.STATUS_PENDING)
        count = 0
        for suggestion in pending:
            suggestion.reject(reason='Не соответствует требованиям', moderator=request.user)
            self._notify_async(suggestion, 'rejected')
            count += 1
        self.message_user(request, f'Отклонено {count} заявок.', messages.WARNING)

    def _notify_async(self, suggestion, action: str):
        """Отправить уведомление пользователю через соответствующую платформу."""
        try:
            bot_config = suggestion.bot
            if action == 'approved':
                text = bot_config.approved_message.replace('{tracking_id}', suggestion.short_tracking_id)
            else:
                text = (
                    bot_config.rejected_message
                    .replace('{tracking_id}', suggestion.short_tracking_id)
                    .replace('{reason}', suggestion.rejection_reason or 'Не соответствует требованиям')
                )

            if bot_config.platform == SuggestionBot.PLATFORM_TELEGRAM:
                import asyncio
                from telegram import Bot
                async def send():
                    bot = Bot(token=bot_config.get_token())
                    async with bot:
                        await bot.send_message(chat_id=suggestion.platform_user_id, text=text)
                asyncio.run(send())

            elif bot_config.platform == SuggestionBot.PLATFORM_VK:
                from .vk.bot import VKSuggestionBot
                vk_bot = VKSuggestionBot(bot_config)
                vk_bot._send(suggestion.platform_user_id, text)

            elif bot_config.platform == SuggestionBot.PLATFORM_MAX:
                from .max_bot.bot import MaxBotAPI
                api = MaxBotAPI(bot_config.get_token())
                api.send_message(suggestion.platform_user_id, text)

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                'Не удалось отправить уведомление для заявки #%s: %s',
                suggestion.short_tracking_id, e
            )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.moderated_by = request.user
        super().save_model(request, obj, form, change)


# ─── SuggestionUserStats Admin ────────────────────────────────────────────────

@admin.register(SuggestionUserStats)
class SuggestionUserStatsAdmin(admin.ModelAdmin):
    list_display = [
        'display_name', 'platform_username', 'bot',
        'total', 'approved', 'rejected', 'pending', 'published',
        'last_submission'
    ]
    list_filter = ['bot']
    search_fields = ['display_name', 'platform_username', 'platform_user_id']
    readonly_fields = [
        'bot', 'platform_user_id', 'platform_username', 'display_name',
        'total', 'approved', 'rejected', 'pending', 'published', 'last_submission'
    ]
    ordering = ['-approved', '-total']
