from django.contrib import admin
from .models import Post, PostMedia, PublishResult

class PostMediaInline(admin.TabularInline):
    model = PostMedia
    extra = 0

@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'author', 'status', 'scheduled_at', 'published_at', 'repeat_enabled']
    list_filter = ['status', 'repeat_enabled']
    search_fields = ['text', 'author__email']
    filter_horizontal = ['channels']
    inlines = [PostMediaInline]
    readonly_fields = [
        'created_at',
        'updated_at',
        'published_at',
        'uid',
        'source_parsed_item',
        'source_parse_keyword',
        'parsing_publish_stats_applied',
    ]

@admin.register(PublishResult)
class PublishResultAdmin(admin.ModelAdmin):
    list_display = ['post', 'channel', 'status', 'published_at']
    list_filter = ['status', 'channel']
    readonly_fields = ['post', 'channel', 'status', 'platform_message_id', 'error_message', 'published_at']
