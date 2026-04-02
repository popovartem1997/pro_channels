from django.contrib import admin
from .models import Channel, ChannelAdAddon, ChannelAdVolumeDiscount, ChannelGroup


@admin.register(ChannelGroup)
class ChannelGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'created_at']
    list_filter = ['owner']
    search_fields = ['name', 'owner__email']


class ChannelAdVolumeDiscountInline(admin.TabularInline):
    model = ChannelAdVolumeDiscount
    extra = 1


class ChannelAdAddonInline(admin.TabularInline):
    model = ChannelAdAddon
    extra = 1


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ['name', 'platform', 'owner', 'subscribers_count', 'is_active', 'ad_enabled', 'token_configured', 'created_at']
    list_filter = ['platform', 'is_active', 'ad_enabled']
    search_fields = ['name', 'owner__email', 'tg_chat_id', 'vk_group_id']
    readonly_fields = ['created_at', 'updated_at', 'last_synced_at', 'subscribers_count']
    inlines = [ChannelAdVolumeDiscountInline, ChannelAdAddonInline]
