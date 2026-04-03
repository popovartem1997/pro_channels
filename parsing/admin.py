from django.contrib import admin, messages

from .models import ParseKeyword, ParsedItem, ParseSource, ParseTask


@admin.register(ParseTask)
class ParseTaskAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'is_active', 'schedule_cron', 'last_run_at', 'items_found_total']
    list_filter = ['is_active']
    search_fields = ['name', 'owner__email']
    filter_horizontal = ['sources', 'keywords']
    actions = ['clear_telethon_redis_locks_action']

    def has_can_clear_telethon_locks_permission(self, request):
        return request.user.has_perm('parsing.can_clear_telethon_locks')

    @admin.action(
        description='Снять зависшие блокировки Telethon (Redis)',
        permissions=['can_clear_telethon_locks'],
    )
    def clear_telethon_redis_locks_action(self, request, queryset):
        from parsing.telethon_locks import clear_telethon_redis_locks

        result = clear_telethon_redis_locks(dry_run=False)
        if not result['ok']:
            self.message_user(request, result['message'], level=messages.ERROR)
            return
        detail = result['message']
        if result.get('keys'):
            preview = ', '.join(result['keys'][:5])
            if len(result['keys']) > 5:
                preview += f' … (+{len(result["keys"]) - 5})'
            detail += f' Ключи: {preview}.'
        self.message_user(request, detail, level=messages.SUCCESS)


@admin.register(ParseSource)
class ParseSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'platform', 'source_id', 'owner', 'is_active']
    list_filter = ['platform', 'is_active']

@admin.register(ParseKeyword)
class ParseKeywordAdmin(admin.ModelAdmin):
    list_display = ['keyword', 'owner', 'is_active', 'created_at']
    filter_horizontal = ['sources']

@admin.register(ParsedItem)
class ParsedItemAdmin(admin.ModelAdmin):
    list_display = ['source', 'status', 'text_preview', 'found_at']
    list_filter = ['status', 'source__platform']
    readonly_fields = ['found_at']

    def text_preview(self, obj):
        return obj.text[:80]
    text_preview.short_description = 'Текст'
