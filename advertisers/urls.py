from django.urls import path
from django.views.generic import RedirectView
from . import views
from . import campaign_views as cv

app_name = 'advertisers'

urlpatterns = [
    path('catalog/', views.catalog, name='catalog'),
    path('register/', views.advertiser_register, name='register'),
    path('', views.advertiser_dashboard, name='dashboard'),
    path(
        'order/new/',
        RedirectView.as_view(pattern_name='advertisers:campaign_new', permanent=True),
    ),
    path('order/<int:pk>/', views.order_detail, name='order_detail'),
    path('campaign/', views.ad_application_list, name='campaign_list'),
    path('campaign/new/', cv.campaign_new, name='campaign_new'),
    path('campaign/<int:pk>/channel/', cv.campaign_draft_channel, name='campaign_draft_channel'),
    path('campaign/<int:pk>/resume/', cv.campaign_resume, name='campaign_resume'),
    path('campaign/<int:pk>/delete/', cv.campaign_delete, name='campaign_delete'),
    path('campaign/<int:pk>/slots/', cv.campaign_slots, name='campaign_slots'),
    path('campaign/<int:pk>/content/', cv.campaign_content, name='campaign_content'),
    path('campaign/<int:pk>/ord/', cv.campaign_ord, name='campaign_ord'),
    path('campaign/<int:pk>/ord/prepare/', cv.campaign_ord_prepare, name='campaign_ord_prepare'),
    path('campaign/<int:pk>/review/', cv.campaign_review, name='campaign_review'),
    path('campaign/<int:pk>/contract/', cv.campaign_contract, name='campaign_contract'),
    path('campaign/<int:pk>/submit-owner/', cv.campaign_submit_to_owner, name='campaign_submit_to_owner'),
    path('campaign/<int:pk>/pending-owner/', cv.campaign_pending_owner, name='campaign_pending_owner'),
    path('campaign/<int:pk>/contacts/', cv.campaign_contacts, name='campaign_contacts'),
    path('campaign/<int:pk>/checkout/', cv.campaign_checkout, name='campaign_checkout'),
    path('campaign/<int:pk>/wait-transfer/', cv.campaign_transfer_wait, name='campaign_transfer_wait'),
    # Панель владельца
    path('manage/', views.owner_orders, name='owner_orders'),
    path('manage/order/<int:pk>/', views.owner_order_detail, name='owner_order_detail'),
    path('manage/campaigns/', views.owner_ad_applications, name='owner_campaigns'),
    path('manage/campaigns/<int:pk>/', cv.owner_campaign_detail, name='owner_campaign_detail'),
    path(
        'manage/campaigns/<int:pk>/confirm-payment/',
        cv.owner_campaign_confirm_payment,
        name='owner_campaign_confirm_payment',
    ),
    path('manage/campaigns/<int:pk>/decision/', cv.owner_campaign_decision, name='owner_campaign_decision'),
    path('manage/<int:pk>/moderate/', views.owner_order_moderate, name='owner_order_moderate'),
    path('manage/<int:order_pk>/act/create/', views.create_act, name='create_act'),
    path('act/<int:pk>/pdf/', views.download_act_pdf, name='act_pdf'),
]
