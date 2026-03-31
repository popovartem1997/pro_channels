from django.urls import path
from . import views

app_name = 'ord_marking'

urlpatterns = [
    path('', views.ord_list, name='list'),
    path('create/', views.ord_create, name='create'),
]
