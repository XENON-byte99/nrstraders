from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    ROLE_CHOICES = (
        ('SUPERADMIN', 'Super Admin'),
        ('ACCOUNTANT', 'Accountant'),
        ('SUPPLIER', 'Supplier'),
        ('OWNER', 'Owner'),
        ('BUYER', 'Buyer'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='BUYER')
    whatsapp_number = models.CharField(max_length=20, blank=True, help_text="Enter number with country code, e.g., 88017...")

    # Permissions
    p_view_bill_summary = models.BooleanField(default=False)
    p_view_bills = models.BooleanField(default=False)
    p_create_bill = models.BooleanField(default=False)
    p_edit_bill = models.BooleanField(default=False)
    p_delete_bill = models.BooleanField(default=False)
    p_price_bill = models.BooleanField(default=False)
    p_approve_bill = models.BooleanField(default=False)
    
    p_manage_inventory = models.BooleanField(default=False)
    p_manage_contacts = models.BooleanField(default=False)
    p_manage_categories = models.BooleanField(default=False)
    
    p_view_gate_entry = models.BooleanField(default=False)
    p_manage_visual_requisitions = models.BooleanField(default=False)
    p_manage_requisitions = models.BooleanField(default=False)
    
    p_view_audit_logs = models.BooleanField(default=False)
    p_manage_authorizations = models.BooleanField(default=False)
    p_manage_users = models.BooleanField(default=False)
    p_manage_peer_transactions = models.BooleanField(default=False)
    p_print_documents = models.BooleanField(default=False)
    
    nid_number = models.CharField(max_length=20, blank=True, null=True, help_text="National ID Number")
    google_drive_json_key = models.TextField(blank=True, null=True, help_text="Google Drive Service Account JSON Key")

class PeerTransaction(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending Confirmation'),
        ('CONFIRMED', 'Confirmed'),
        ('CANCELLED', 'Cancelled'),
    )
    
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='peer_sent_transactions')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='peer_received_transactions')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField(blank=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    initiator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='initiated_peer_transactions')
    
    sender_confirmed = models.BooleanField(default=False)
    receiver_confirmed = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.sender.username} -> {self.receiver.username} ({self.amount})"
