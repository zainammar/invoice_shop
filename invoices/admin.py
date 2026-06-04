from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Invoice,
    InvoiceItem,
    UserProduct,
    PaymentProof,
)


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 1


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'invoice_number',
        'customer_name',
        'user',
        'created_at',
    )
    inlines = [InvoiceItemInline]


@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = (
        'invoice',
        'sr_no',
        'product_name',
        'quantity',
        'unit_price',
        'amount',
    )


@admin.register(UserProduct)
class UserProductAdmin(admin.ModelAdmin):
    list_display = (
        'product_name',
        'unit_price',
        'user',
        'created_at',
    )
    list_filter = ('user',)


@admin.register(PaymentProof)
class PaymentProofAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'amount',
        'view_file',
        'is_approved',
        'uploaded_at',
    )

    list_filter = (
        'is_approved',
        'uploaded_at',
    )

    search_fields = (
        'user__username',
    )

    def view_file(self, obj):
        if obj.proof_file:
            return format_html(
                '<a href="{}" target="_blank">Open File</a>',
                obj.proof_file.url
            )
        return "-"
    
    view_file.short_description = "Proof"