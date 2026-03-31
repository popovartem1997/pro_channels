from django.contrib import admin
from .models import ParseSource, ParseKeyword, ParsedItem

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
