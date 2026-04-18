import os
import sys
import django

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nrs_project.settings')
django.setup()

from accounts.models import User

def init_permissions():
    print("--- Initializing User Permissions ---")
    users = User.objects.all()
    
    for user in users:
        print(f"Processing user: {user.username} (Role: {user.role})")
        
        # SuperAdmin and Owner get everything
        if user.is_superuser or user.role == 'OWNER':
            user.p_view_bill_summary = True
            user.p_view_bills = True
            user.p_create_bill = True
            user.p_edit_bill = True
            user.p_delete_bill = True
            user.p_price_bill = True
            user.p_approve_bill = True
            user.p_manage_inventory = True
            user.p_manage_contacts = True
            user.p_manage_categories = True
            user.p_view_gate_entry = True
            user.p_manage_visual_requisitions = True
            user.p_manage_requisitions = True
            user.p_view_audit_logs = True
            user.p_manage_users = True
            
        elif user.role == 'ACCOUNTANT':
            user.p_view_bill_summary = True
            user.p_view_bills = True
            user.p_create_bill = True
            user.p_edit_bill = True
            user.p_delete_bill = True
            user.p_price_bill = True
            user.p_manage_inventory = True
            user.p_manage_contacts = True
            user.p_manage_categories = True
            user.p_view_gate_entry = True
            user.p_view_audit_logs = True
            user.p_manage_users = True # Accountants can currently manage users in this system
            
        elif user.role == 'SUPPLIER':
            user.p_view_bills = True
            user.p_price_bill = True
            user.p_view_gate_entry = True
            user.p_view_audit_logs = True
            
        elif user.role == 'BUYER':
            user.p_view_bills = True
            user.p_manage_requisitions = True
            user.p_view_gate_entry = True

        user.save()
        print(f"Done for {user.username}")

    print("\n--- Permissions Initialized Successfully! ---")

if __name__ == "__main__":
    init_permissions()
