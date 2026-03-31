from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Subscription, EmailVerification


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('email', 'username', 'get_full_name', 'role', 'is_email_verified',
                    'is_on_trial', 'trial_days_left', 'date_joined', 'is_active')
    list_filter = ('role', 'is_email_verified', 'is_active', 'is_staff')
    search_fields = ('email', 'username', 'first_name', 'last_name')
    ordering = ('-date_joined',)
    readonly_fields = ('date_joined', 'last_login', 'trial_days_left')
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Платформа', {
            'fields': ('role', 'phone', 'company', 'avatar',
                       'is_email_verified', 'trial_ends_at', 'invited_by')
        }),
    )


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'is_active', 'starts_at', 'ends_at')
    list_filter = ('plan', 'is_active')
    search_fields = ('user__email', 'user__username')
    ordering = ('-created_at',)


@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'created_at', 'is_used')
    list_filter = ('is_used',)
    search_fields = ('user__email',)
