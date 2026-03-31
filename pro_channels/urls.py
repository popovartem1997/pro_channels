from django.contrib import admin
from django.urls import path, include
from core import views as core_views
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from core.sitemaps import StaticViewSitemap

urlpatterns = [
    path('admin/', admin.site.urls),

    path('sitemap.xml', sitemap, {'sitemaps': {'static': StaticViewSitemap}}, name='sitemap'),

    # Приложения проекта
    path('channels/', include('channels.urls', namespace='channels')),
    path('posts/', include('content.urls', namespace='content')),
    path('parsing/', include('parsing.urls', namespace='parsing')),
    path('stats/', include('stats.urls', namespace='stats')),
    path('bots/', include('bots.urls', namespace='bots')),
    path('billing/', include('billing.urls', namespace='billing')),
    path('managers/', include('managers.urls', namespace='managers')),
    path('advertisers/', include('advertisers.urls', namespace='advertisers')),
    path('ord/', include('ord_marking.urls', namespace='ord_marking')),

    # Accounts (без префикса — login, register, dashboard и т.д.)
    path('', include('accounts.urls')),

    # Core: лендинг, SEO, оферта
    path('', include('core.urls', namespace='core')),
    path('', core_views.home, name='home'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
