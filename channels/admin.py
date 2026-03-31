from django.contrib import admin
from .models import Channel

@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ['name', 'platform', 'owner', 'subscribers_count', 'is_active', 'token_configured', 'created_at']
    list_filter = ['platform', 'is_active']
    search_fields = ['name', 'owner__email', 'tg_chat_id', 'vk_group_id']
    readonly_fields = ['created_at', 'updated_at', 'last_synced_at', 'subscribers_count']
