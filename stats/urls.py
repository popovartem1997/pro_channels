from django.urls import path
from . import views

app_name = 'stats'

urlpatterns = [
    path('', views.stats_dashboard, name='dashboard'),
    path('channel/<int:channel_pk>/', views.channel_stats, name='channel'),
]
