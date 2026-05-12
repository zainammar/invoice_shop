from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    # Product Catalog
    path('products/', views.products_view, name='products'),
    path('products/<int:pk>/edit/', views.edit_product, name='edit_product'),
    path('products/<int:pk>/delete/', views.delete_product, name='delete_product'),
    path('api/products/', views.products_json, name='products_json'),
    # Invoices
    path('invoice/new/', views.create_invoice, name='create_invoice'),
    path('invoice/<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('invoice/<int:pk>/edit/', views.edit_invoice, name='edit_invoice'),
    path('invoice/<int:pk>/delete/', views.delete_invoice, name='delete_invoice'),
    path('invoice/<int:pk>/pdf/', views.download_pdf, name='download_pdf'),
]
