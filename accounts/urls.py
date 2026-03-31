from django.urls import path
from . import views

urlpatterns = [
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('verify-email/<uuid:token>/', views.verify_email, name='verify_email'),
    path('reset-password/', views.reset_password_request, name='reset_password'),
    path('reset-password/<uuid:token>/', views.reset_password_confirm, name='reset_password_confirm'),
    path('change-password/', views.change_password, name='change_password'),
]
