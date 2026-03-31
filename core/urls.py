from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('robots.txt', views.robots_txt, name='robots_txt'),
    path('offer/', views.offer, name='offer'),
    path('privacy/', views.privacy, name='privacy'),
    path('quickstart/', views.quickstart, name='quickstart'),
  path('settings/api-keys/', views.api_keys, name='api_keys'),
]
