from django.contrib import admin
from .models import ChannelStat, PostStat

@admin.register(ChannelStat)
class ChannelStatAdmin(admin.ModelAdmin):
    list_display = ['channel', 'date', 'subscribers', 'views', 'er', 'posts_count']
    list_filter = ['channel']
    readonly_fields = ['channel', 'date', 'subscribers', 'views', 'er', 'posts_count']

@admin.register(PostStat)
class PostStatAdmin(admin.ModelAdmin):
    list_display = ['post', 'channel', 'views', 'reactions', 'forwards', 'synced_at']
    readonly_fields = ['post', 'channel', 'views', 'reactions', 'forwards', 'comments', 'synced_at']
