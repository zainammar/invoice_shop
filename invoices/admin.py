from django.contrib import admin
from .models import Invoice, InvoiceItem

class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 1

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'customer_name', 'user', 'created_at']
    inlines = [InvoiceItemInline]

@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'sr_no', 'product_name', 'quantity', 'unit_price', 'amount']

from .models import UserProduct
@admin.register(UserProduct)
class UserProductAdmin(admin.ModelAdmin):
    list_display = ['product_name', 'unit_price', 'user', 'created_at']
    list_filter = ['user']
