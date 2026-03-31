from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    path('subscribe/', views.subscribe, name='subscribe'),
    path('invoice/create/', views.create_invoice, name='create_invoice'),
    path('invoice/<int:pk>/pay/', views.pay_invoice, name='pay'),
    path('webhook/tbank/', views.tbank_webhook, name='tbank_webhook'),
    path('invoices/', views.invoices_list, name='invoices'),
    path('success/', views.payment_success, name='payment_success'),
    path('fail/', views.payment_fail, name='payment_fail'),
    path('invoice/<int:pk>/pdf/', views.download_invoice_pdf, name='invoice_pdf'),
]
