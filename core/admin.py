from django.contrib import admin

from .models import GlobalApiKeys, PageVisit


@admin.register(GlobalApiKeys)
class GlobalApiKeysAdmin(admin.ModelAdmin):
    list_display = ["id", "updated_at"]


@admin.register(PageVisit)
class PageVisitAdmin(admin.ModelAdmin):
    list_display = ["created_at", "user", "method", "path", "status_code", "duration_ms", "ip"]
    list_filter = ["method", "status_code", "created_at"]
    search_fields = ["path", "query_string", "user__username", "ip"]
    readonly_fields = ["created_at"]

