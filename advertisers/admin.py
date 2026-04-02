from django.contrib import admin

from .models import AdApplication, AdvertisingSlot, Advertiser, AdvertisingOrder, Act


@admin.register(AdApplication)
class AdApplicationAdmin(admin.ModelAdmin):
    list_display = ('pk', 'advertiser', 'channel', 'status', 'total_amount', 'created_at')
    list_filter = ('status', 'channel')
    search_fields = ('advertiser__company_name', 'advertiser__inn')
    raw_id_fields = ('advertiser', 'channel', 'post', 'invoice')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AdvertisingSlot)
class AdvertisingSlotAdmin(admin.ModelAdmin):
    list_display = ('channel', 'starts_at', 'application')
    list_filter = ('channel',)
    raw_id_fields = ('application',)


class ActInline(admin.TabularInline):
    model = Act
    extra = 0


@admin.register(AdvertisingOrder)
class AdvertisingOrderAdmin(admin.ModelAdmin):
    list_display = ('pk', 'advertiser', 'title', 'status', 'budget', 'created_at')
    inlines = [ActInline]


@admin.register(Advertiser)
class AdvertiserAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'inn', 'user', 'created_at')
    search_fields = ('company_name', 'inn', 'user__email')
