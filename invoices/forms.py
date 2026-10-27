from decimal import Decimal
from django import forms
from .models import Payment

class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["amount", "payment_method", "note"]

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= Decimal("0"):
            raise forms.ValidationError("Amount 0 se zyada honi chahiye.")
        if self.invoice and amount > self.invoice.balance_due():
            raise forms.ValidationError(
                f"Amount balance ({self.invoice.balance_due()}) se zyada nahi ho sakti."
            )
        return amount