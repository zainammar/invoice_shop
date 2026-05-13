from django.db import models
from django.contrib.auth.models import User


class UserProduct(models.Model):
    """User's personal product catalog — one-time entry"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='products')
    product_name = models.CharField(max_length=300)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['product_name']
        unique_together = ['user', 'product_name']

    def __str__(self):
        return f"{self.product_name} — Rs. {self.unit_price}"


class Invoice(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invoices')
    invoice_number = models.CharField(max_length=50, unique=True)
    customer_name = models.CharField(max_length=200)
    customer_email = models.CharField(max_length=200, blank=True)
    customer_address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def total_amount(self):
        return sum(item.amount for item in self.items.all())

    def __str__(self):
        return f"Invoice #{self.invoice_number} - {self.customer_name}"


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(UserProduct, on_delete=models.SET_NULL, null=True, blank=True)
    sr_no = models.PositiveIntegerField()
    product_name = models.CharField(max_length=300)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ['sr_no']

    def save(self, *args, **kwargs):
        self.amount = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sr_no}. {self.product_name}"


class CompanyProfile(models.Model):
    """One company profile per user"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='company')
    company_name = models.CharField(max_length=200, default='My Company')
    company_logo = models.ImageField(upload_to='logos/company/', blank=True, null=True)
    user_avatar = models.ImageField(upload_to='logos/avatars/', blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.company_name} ({self.user.username})"
