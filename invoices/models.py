from decimal import Decimal

from django.conf import settings
from django.db import models


class PaymentProof(models.Model):
    
    invoice = models.ForeignKey(
    "Invoice",
    on_delete=models.CASCADE,
    related_name="payment_proofs",
    null=True,
    blank=True,
 )

    payment_method = models.CharField(max_length=100, blank=True)
    transaction_id = models.CharField(max_length=255, blank=True)
    proof_image = models.ImageField(
        upload_to="payment_proofs/",
        blank=True,
        null=True,
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment proof for {self.invoice.invoice_number}"


class UserProduct(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="products",
    )
    product_name = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
 
    def __str__(self):
        return self.product_name


class Invoice(models.Model):
    DISCOUNT_PERCENT = "percent"
    DISCOUNT_FIXED = "fixed"

    DISCOUNT_CHOICES = [
        (DISCOUNT_PERCENT, "Percentage"),
        (DISCOUNT_FIXED, "Fixed Amount"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    invoice_number = models.CharField(max_length=50, unique=True)
    customer_name = models.CharField(max_length=255)
    customer_email = models.EmailField(blank=True)
    customer_address = models.TextField(blank=True)

    discount_type = models.CharField(
        max_length=10,
        choices=DISCOUNT_CHOICES,
        default=DISCOUNT_PERCENT,
    )
    discount_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def subtotal(self):
        """Total of invoice items after item-level discounts."""
        return sum(
            (item.amount for item in self.items.all()),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))

    def discount_amount(self):
        """Calculate invoice-level discount."""
        subtotal = self.subtotal()
        value = max(self.discount_value or Decimal("0.00"), Decimal("0.00"))

        if self.discount_type == self.DISCOUNT_PERCENT:
            value = min(value, Decimal("100.00"))
            discount = subtotal * value / Decimal("100.00")
        else:
            discount = value

        discount = min(max(discount, Decimal("0.00")), subtotal)

        return discount.quantize(Decimal("0.01"))

    def grand_total(self):
        """Final payable amount after invoice discount."""
        return max(
            self.subtotal() - self.discount_amount(),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))

    def total_amount(self):
        """Return the final payable amount for PDF and invoice totals."""
        return self.grand_total()

    def __str__(self):
        return self.invoice_number


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        UserProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    sr_no = models.PositiveIntegerField(default=1)
    product_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    def save(self, *args, **kwargs):
        """Calculate item amount after its percentage discount."""
        quantity = Decimal(str(self.quantity or 0))
        unit_price = Decimal(str(self.unit_price or 0))
        discount_percent = Decimal(str(self.discount_percent or 0))

        discount_percent = min(
            max(discount_percent, Decimal("0.00")),
            Decimal("100.00"),
        )

        gross_amount = quantity * unit_price
        discount = gross_amount * discount_percent / Decimal("100.00")

        self.amount = (gross_amount - discount).quantize(Decimal("0.01"))
        self.discount_percent = discount_percent

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product_name} — {self.invoice.invoice_number}"


class CompanyProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="company",
    )
    company_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    company_logo = models.ImageField(
        upload_to="company_logos/",
        blank=True,
        null=True,
    )
    user_avatar = models.ImageField(
        upload_to="user_avatars/",
        blank=True,
        null=True,
    )

    def __str__(self):
        return self.company_name