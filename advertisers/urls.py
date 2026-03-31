from django.urls import path
from . import views

app_name = 'advertisers'

urlpatterns = [
    path('catalog/', views.catalog, name='catalog'),
    path('register/', views.advertiser_register, name='register'),
    path('', views.advertiser_dashboard, name='dashboard'),
    path('order/new/', views.order_create, name='order_create'),
    path('order/<int:pk>/', views.order_detail, name='order_detail'),
    # Панель владельца
    path('manage/', views.owner_orders, name='owner_orders'),
    path('manage/<int:pk>/moderate/', views.owner_order_moderate, name='owner_order_moderate'),
    path('manage/<int:order_pk>/act/create/', views.create_act, name='create_act'),
    path('act/<int:pk>/pdf/', views.download_act_pdf, name='act_pdf'),
]
