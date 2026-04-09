from django.urls import path
from . import views

app_name = 'parsing'

urlpatterns = [
    path('', views.sources_list, name='sources'),
    path('fragments/panels/', views.sources_list_fragments, name='sources_fragments'),
    path('telethon/connect/', views.telethon_connect, name='telethon_connect'),
    path('telethon/disconnect/', views.telethon_disconnect, name='telethon_disconnect'),
    path('source/add/', views.source_create, name='source_create'),
    path('source/<int:pk>/delete/', views.source_delete, name='source_delete'),
    path('keyword/add/', views.keyword_create, name='keyword_create'),
    path('keyword/<int:pk>/edit/', views.keyword_edit, name='keyword_edit'),
    path('keyword/<int:pk>/delete/', views.keyword_delete, name='keyword_delete'),
    path('items/', views.parsed_items, name='items'),
    path('items/clear/', views.parsed_items_clear, name='items_clear'),
    path('items/<int:pk>/skip/', views.item_skip, name='item_skip'),
    path('items/<int:pk>/delete/', views.item_delete, name='item_delete'),
    path('items/<int:pk>/to-post/', views.item_to_post, name='item_to_post'),
    path('items/<int:pk>/ai-to-post/', views.item_ai_to_post, name='item_ai_to_post'),
    path('feed-ai-moods/save/', views.feed_ai_moods_save, name='feed_ai_moods_save'),
    path('tasks/', views.parse_tasks_list, name='parse_tasks'),
    path('tasks/create/', views.parse_task_create, name='parse_task_create'),
    path('tasks/<int:pk>/run/', views.parse_task_run, name='parse_task_run'),
    path('tasks/<int:pk>/delete/', views.parse_task_delete, name='parse_task_delete'),
    path('ai/', views.ai_rewrite_list, name='ai_rewrite'),
    path('ai/create/', views.ai_rewrite_create, name='ai_rewrite_create'),
    path('keywords/harvest/', views.keyword_harvest_list, name='keyword_harvest_list'),
    path('keywords/harvest/new/', views.keyword_harvest_create, name='keyword_harvest_create'),
    path('keywords/harvest/<int:pk>/', views.keyword_harvest_detail, name='keyword_harvest_detail'),
]
