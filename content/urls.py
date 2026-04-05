from django.urls import path
from . import views

app_name = 'content'

urlpatterns = [
    path('', views.post_list, name='list'),
    path('more/', views.post_list_more, name='list_more'),
    path('create/', views.post_create, name='create'),
    path('from-suggestion/<uuid:tracking_id>/', views.post_create_from_suggestion, name='create_from_suggestion'),
    path('from-suggestion/<uuid:tracking_id>/ai/', views.post_ai_from_suggestion, name='ai_from_suggestion'),
    path('<int:pk>/', views.post_detail, name='detail'),
    path('<int:pk>/media/<int:media_pk>/download/', views.post_media_download, name='media_download'),
    path('<int:pk>/edit/', views.post_edit, name='edit'),
    path('<int:pk>/delete/', views.post_delete, name='delete'),
    path('<int:pk>/publish/', views.post_publish_now, name='publish_now'),
]
