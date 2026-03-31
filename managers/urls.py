from django.urls import path
from . import views

app_name = 'managers'

urlpatterns = [
    path('', views.team_list, name='list'),
    path('invite/', views.team_invite, name='invite'),
    path('accept/<uuid:token>/', views.accept_invite, name='accept_invite'),
    path('<int:pk>/remove/', views.member_remove, name='remove'),
    path('invite/<int:pk>/cancel/', views.invite_cancel, name='cancel_invite'),
]
