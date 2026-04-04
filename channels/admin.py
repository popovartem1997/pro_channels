from django.contrib import admin
from django.utils.html import escape, format_html
from .models import (
    Channel,
    ChannelAdAddon,
    ChannelAdVolumeDiscount,
    ChannelGroup,
    ChannelInterestingFacts,
    ChannelMorningDigest,
    HistoryImportRun,
)


@admin.register(ChannelGroup)
class ChannelGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'created_at']
    list_filter = ['owner']
    search_fields = ['name', 'owner__email']


class ChannelAdVolumeDiscountInline(admin.TabularInline):
    model = ChannelAdVolumeDiscount
    extra = 1


class ChannelAdAddonInline(admin.TabularInline):
    """
    Расширенное редактирование (staff). Владелец настраивает стандартные опции
    top_1h / pin_24h в интерфейсе «Редактировать канал».
    """

    model = ChannelAdAddon
    extra = 1
    fields = (
        'code',
        'title',
        'addon_kind',
        'price',
        'block_hours',
        'max_pin_hours',
        'top_duration_minutes',
        'is_active',
    )


@admin.register(ChannelInterestingFacts)
class ChannelInterestingFactsAdmin(admin.ModelAdmin):
    list_display = ['channel', 'is_enabled', 'interval_hours', 'last_generated_at']
    list_filter = ['is_enabled']
    search_fields = ['channel__name', 'topic']
    raw_id_fields = ['channel']


@admin.register(ChannelMorningDigest)
class ChannelMorningDigestAdmin(admin.ModelAdmin):
    list_display = ['channel', 'is_enabled', 'send_time', 'timezone_name', 'last_sent_on']
    list_filter = ['is_enabled']
    search_fields = ['channel__name']
    raw_id_fields = ['channel']


@admin.register(HistoryImportRun)
class HistoryImportRunAdmin(admin.ModelAdmin):
    list_display = [
        'pk',
        'status',
        'source_channel',
        'target_channel',
        'download_tg_media',
        'created_by',
        'celery_task_id_short',
        'started_at',
        'finished_at',
        'progress_sent',
    ]
    list_filter = ['status']
    search_fields = ['celery_task_id', 'error_message', 'source_channel__name', 'target_channel__name']
    readonly_fields = [
        'created_by',
        'source_channel',
        'target_channel',
        'status',
        'started_at',
        'finished_at',
        'progress_json',
        'journal_formatted',
        'error_message',
        'cancel_requested',
        'celery_task_id',
        'created_at',
        'updated_at',
    ]
    raw_id_fields = ['created_by', 'source_channel', 'target_channel']

    @admin.display(description='Celery id')
    def celery_task_id_short(self, obj):
        s = (obj.celery_task_id or '').strip()
        return (s[:16] + '…') if len(s) > 18 else s or '—'

    @admin.display(description='Отправлено')
    def progress_sent(self, obj):
        pj = obj.progress_json or {}
        return pj.get('sent', '—')

    @admin.display(description='Журнал')
    def journal_formatted(self, obj):
        lines = (obj.progress_json or {}).get('journal') or []
        if not lines:
            return '—'
        parts = []
        for row in lines[-30:]:
            t = escape(str(row.get('t', '')))
            m = escape(str(row.get('msg', '')))
            parts.append(f'{t}  {m}')
        return format_html('<pre style="white-space:pre-wrap;max-height:320px;overflow:auto;">{}</pre>', '\n'.join(parts))

    def has_add_permission(self, request):
        return False


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ['name', 'platform', 'owner', 'subscribers_count', 'is_active', 'ad_enabled', 'token_configured', 'created_at']
    list_filter = ['platform', 'is_active', 'ad_enabled']
    search_fields = ['name', 'owner__email', 'tg_chat_id', 'vk_group_id']
    readonly_fields = ['created_at', 'updated_at', 'last_synced_at', 'subscribers_count']
    inlines = [ChannelAdVolumeDiscountInline, ChannelAdAddonInline]
