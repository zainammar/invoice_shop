from django.contrib import admin
from .models import (
    PaymentProof,
    UserProduct,
    Invoice,
    InvoiceItem,
    CompanyProfile,
)


@admin.register(PaymentProof)
class PaymentProofAdmin(admin.ModelAdmin):
    list_display = (
        "invoice",
        "payment_method",
        "transaction_id",
        "uploaded_at",
    )
    list_filter = (
        "payment_method",
        "uploaded_at",
    )
    search_fields = (
        "invoice__invoice_number",
        "invoice__customer_name",
        "transaction_id",
    )
    readonly_fields = ("uploaded_at",)


@admin.register(UserProduct)
class UserProductAdmin(admin.ModelAdmin):
    list_display = (
        "product_name",
        "user",
        "unit_price",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = (
        "product_name",
        "user__username",
    )


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    readonly_fields = ("amount",)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "customer_name",
        "user",
        "created_at",
        "total_amount",
    )
    search_fields = (
        "invoice_number",
        "customer_name",
        "user__username",
    )
    list_filter = ("created_at",)
    inlines = [InvoiceItemInline]


@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = (
        "invoice",
        "sr_no",
        "product_name",
        "quantity",
        "unit_price",
        "discount_percent",
        "amount",
    )
    search_fields = (
        "product_name",
        "invoice__invoice_number",
    )


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "user",
        "phone",
        "email",
    )
    search_fields = (
        "company_name",
        "user__username",
        "email",
    )
