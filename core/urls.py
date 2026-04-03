from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('robots.txt', views.robots_txt, name='robots_txt'),
    path('offer/', views.offer, name='offer'),
    path('privacy/', views.privacy, name='privacy'),
    path('quickstart/', views.quickstart, name='quickstart'),
    path('feed/', views.feed, name='feed'),
    path('settings/api-keys/', views.api_keys, name='api_keys'),
    path('settings/audit/', views.audit_log, name='audit_log'),
    path('settings/celery/', views.celery_monitor, name='celery_monitor'),
    path('settings/celery/snapshot/', views.celery_monitor_snapshot, name='celery_monitor_snapshot'),
]
