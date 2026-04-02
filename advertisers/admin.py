from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum
from django.template.response import TemplateResponse
from django.urls import path

from .models import AdApplication, AdvertisingSlot, Advertiser, AdvertisingOrder, Act


@admin.register(AdApplication)
class AdApplicationAdmin(admin.ModelAdmin):
    change_list_template = 'admin/advertisers/adapplication/change_list.html'
    list_display = ('pk', 'advertiser', 'channel', 'status', 'total_amount', 'created_at')
    list_filter = ('status', 'channel')
    search_fields = ('advertiser__company_name', 'advertiser__inn')
    raw_id_fields = ('advertiser', 'channel', 'post', 'invoice')
    readonly_fields = ('created_at', 'updated_at', 'ord_wizard_saved_at')

    def get_urls(self):
        info = self.opts.app_label, self.opts.model_name
        return [
            path(
                'analytics/',
                self.admin_site.admin_view(self.ad_campaign_analytics),
                name='%s_%s_analytics' % info,
            ),
        ] + super().get_urls()

    def ad_campaign_analytics(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        from content.models import Post
        from ord_marking.models import ORDRegistration

        paid_like = [
            AdApplication.STATUS_PAID,
            AdApplication.STATUS_SCHEDULED,
            AdApplication.STATUS_PUBLISHED,
            AdApplication.STATUS_COMPLETED,
        ]
        qs_paid = AdApplication.objects.filter(status__in=paid_like)
        totals = qs_paid.aggregate(revenue=Sum('total_amount'), apps=Count('id'))
        total_revenue = totals['revenue'] or Decimal('0')
        total_apps = totals['apps'] or 0

        by_channel = (
            qs_paid.values('channel__id', 'channel__name')
            .annotate(revenue=Sum('total_amount'), cnt=Count('id'))
            .order_by('-revenue', 'channel__name')
        )

        pending = AdApplication.objects.filter(status=AdApplication.STATUS_AWAITING_PAYMENT).aggregate(
            c=Count('id'), s=Sum('total_amount')
        )
        pending_count = pending['c'] or 0
        pending_sum = pending['s'] or Decimal('0')

        draft_count = AdApplication.objects.filter(status=AdApplication.STATUS_DRAFT).count()

        campaign_post_ids = list(
            Post.objects.filter(campaign_application__status__in=paid_like).values_list('pk', flat=True)
        )
        campaign_posts_total = len(campaign_post_ids)
        if campaign_post_ids:
            campaign_posts_with_ord = (
                ORDRegistration.objects.filter(post_id__in=campaign_post_ids)
                .values('post_id')
                .distinct()
                .count()
            )
        else:
            campaign_posts_with_ord = 0
        ord_pct = (
            round(100.0 * campaign_posts_with_ord / campaign_posts_total, 1)
            if campaign_posts_total
            else 0
        )

        context = {
            **self.admin_site.each_context(request),
            'title': 'Аналитика кампаний',
            'total_revenue': total_revenue,
            'total_apps': total_apps,
            'by_channel': list(by_channel),
            'pending_count': pending_count,
            'pending_sum': pending_sum,
            'draft_count': draft_count,
            'campaign_posts_total': campaign_posts_total,
            'campaign_posts_with_ord': campaign_posts_with_ord,
            'ord_pct': ord_pct,
        }
        return TemplateResponse(request, 'admin/advertisers/adapplication/ad_analytics.html', context)


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
