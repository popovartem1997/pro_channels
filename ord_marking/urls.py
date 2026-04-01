from django.urls import path
from . import views

app_name = 'ord_marking'

urlpatterns = [
    path('', views.ord_dashboard, name='list'),
    path('dashboard/', views.ord_dashboard, name='dashboard'),
    path('create/', views.ord_create, name='create'),
    path('<int:pk>/', views.ord_detail, name='detail'),
    path('<int:pk>/retry/', views.ord_retry, name='retry'),
    path('<int:pk>/refresh-erid/', views.ord_refresh_erid, name='refresh_erid'),
    path('<int:pk>/submit-stats/', views.ord_submit_stats, name='submit_stats'),
    path('<int:pk>/snippet.txt', views.ord_copy_snippet, name='snippet'),
]
