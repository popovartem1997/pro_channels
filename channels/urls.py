from django.urls import path
from . import views

app_name = 'channels'

urlpatterns = [
    path('', views.channel_list, name='list'),
    path('groups/add/', views.channel_group_create, name='group_create'),
    path('groups/<int:pk>/edit/', views.channel_group_edit, name='group_edit'),
    path('groups/<int:pk>/delete/', views.channel_group_delete, name='group_delete'),
    path('<int:pk>/set-group/', views.channel_set_group, name='set_group'),
    path('add/', views.channel_create, name='create'),
    path('<int:pk>/', views.channel_detail, name='detail'),
    path('<int:pk>/edit/', views.channel_edit, name='edit'),
    path('<int:pk>/delete/', views.channel_delete, name='delete'),
    path('<int:pk>/test/', views.channel_test, name='test'),
    path('<int:pk>/import-history/', views.channel_import_history, name='import_history'),
    path('import-history/start/', views.import_history_start, name='import_history_start'),
    path('import-history/status/<int:pk>/', views.import_history_status, name='import_history_status'),
    path('import-history/stop/<int:pk>/', views.import_history_stop, name='import_history_stop'),
]
