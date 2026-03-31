from django.urls import path
from . import views

app_name = 'bots'

urlpatterns = [
    # Управление ботами
    path('', views.bot_list, name='list'),
    path('create/', views.bot_create, name='create'),
    path('<int:bot_id>/', views.bot_detail, name='detail'),
    path('suggestions/', views.suggestions_list, name='suggestions'),
    path('suggestions/<int:pk>/moderate/', views.suggestion_moderate, name='moderate'),

    # Webhooks
    path('webhook/telegram/<int:bot_id>/', views.telegram_webhook, name='telegram_webhook'),
    path('webhook/vk/<int:bot_id>/', views.vk_webhook, name='vk_webhook'),
    path('webhook/max/<int:bot_id>/', views.max_webhook, name='max_webhook'),

    # Публичный лидерборд
    path('<int:bot_id>/leaderboard/', views.leaderboard, name='leaderboard'),
]
