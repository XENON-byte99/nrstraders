import os
import django
import sys

# Set up Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nrs_project.settings')
django.setup()

from documents.models import Transaction, VisualRequisition, CheckAuthorization, Product, TransactionCategory, BusinessParty
from audit.models import AuditLog

def reset_system():
    print("--- Starting Data Wipe ---")
    
    # 1. Transactions & Items (Cascade)
    t_count = Transaction.objects.count()
    print(f"Deleting {t_count} Transactions/Bills...")
    Transaction.objects.all().delete()
    
    # 2. Visual Requisitions
    vr_count = VisualRequisition.objects.count()
    print(f"Deleting {vr_count} Visual Requisitions...")
    VisualRequisition.objects.all().delete()
    
    # 3. Check Authorizations
    ca_count = CheckAuthorization.objects.count()
    print(f"Deleting {ca_count} Check Authorizations...")
    CheckAuthorization.objects.all().delete()
    
    # 4. Audit Logs
    al_count = AuditLog.objects.count()
    print(f"Clearing {al_count} Audit Logs...")
    AuditLog.objects.all().delete()
    
    print("\n--- Summary ---")
    print(f"Remaining Products: {Product.objects.count()}")
    print(f"Remaining Categories: {TransactionCategory.objects.count()}")
    print(f"Remaining Business Parties: {BusinessParty.objects.count()}")
    print("\nReset Complete. System is ready for real-time data.")

if __name__ == "__main__":
    reset_system()
