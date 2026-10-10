from django.db import models
from accounts.models import CustomerProfile

class SharedRaffleTicket(models.Model):
    customer = models.ForeignKey(CustomerProfile, on_delete=models.CASCADE, related_name="tickets")
    loyverse_receipt_number = models.CharField(max_length=255, unique=True, help_text="Synced Receipt ID from Loyverse API")
    ticket_code = models.CharField(max_length=100, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.ticket_code} for {self.customer.full_name}"
