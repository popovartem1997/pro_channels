from django.urls import path
from . import views

app_name = 'content'

urlpatterns = [
    path('', views.post_list, name='list'),
    path('create/', views.post_create, name='create'),
    path('from-suggestion/<uuid:tracking_id>/', views.post_create_from_suggestion, name='create_from_suggestion'),
    path('tg-import/', views.tg_import_link, name='tg_import_link'),
    path('tg-import/webhook/', views.tg_import_webhook, name='tg_import_webhook'),
    path('tg-import/webhook/setup/', views.tg_import_webhook_setup, name='tg_import_webhook_setup'),
    path('tg-import/webhook/disable/', views.tg_import_webhook_disable, name='tg_import_webhook_disable'),
    path('tg-import/messages.json', views.tg_import_messages_json, name='tg_import_messages'),
    path('<int:pk>/', views.post_detail, name='detail'),
    path('<int:pk>/edit/', views.post_edit, name='edit'),
    path('<int:pk>/delete/', views.post_delete, name='delete'),
    path('<int:pk>/publish/', views.post_publish_now, name='publish_now'),
]
