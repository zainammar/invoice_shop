from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('invoice/new/', views.create_invoice, name='create_invoice'),
    path('invoice/<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('invoice/<int:pk>/edit/', views.edit_invoice, name='edit_invoice'),
    path('invoice/<int:pk>/delete/', views.delete_invoice, name='delete_invoice'),
    path('invoice/<int:pk>/pdf/', views.download_pdf, name='download_pdf'),
]
