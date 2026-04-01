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
]
