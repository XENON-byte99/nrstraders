import os
import sys
import django
import decimal

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nrs_project.settings')
django.setup()

from documents.models import Transaction, TransactionItem, TransactionCategory
from audit.models import AuditLog
from django.utils import timezone

def test_whatsapp_message():
    print("--- Starting WhatsApp Message Test ---")
    
    # 1. Create a dummy category
    cat, _ = TransactionCategory.objects.get_or_create(name="Test Category")
    
    # 2. Create a dummy transaction
    tx = Transaction.objects.create(
        transaction_category=cat,
        status='PRICED',
        invoice_number='TEST-001',
        vat_percentage=decimal.Decimal('15.00'),
        tax_percentage=decimal.Decimal('5.00'),
        duty_percentage=decimal.Decimal('5.00'),
        profit_margin=decimal.Decimal('10.00'),
        service_charge_percentage=decimal.Decimal('2.00')
    )
    
    # 3. Add items
    TransactionItem.objects.create(
        transaction=tx,
        description="Item A",
        quantity=2,
        base_price=decimal.Decimal('100.00')
    )
    TransactionItem.objects.create(
        transaction=tx,
        description="Item B",
        quantity=1,
        base_price=decimal.Decimal('50.00')
    )
    
    # 4. Create an AuditLog entry
    AuditLog.objects.create(
        transaction=tx,
        action="Updated Prices",
        details="Supplier submitted locked values for Bill #TEST-001. Edits: Item A: ৳0 -> ৳100, Item B: ৳0 -> ৳50"
    )
    
    # 5. Generate message
    msg = tx.get_whatsapp_message()
    
    # Save to file to avoid console encoding issues
    with open('scratch/msg_output.txt', 'w', encoding='utf-8') as f:
        f.write(msg)
    
    print("\nGenerated Message saved to scratch/msg_output.txt\n")
    
    # 6. Basic Assertions (simulation)
    assert "Base: BDT 100.00" in msg
    assert "Profit Margin: 10.00%" in msg
    assert "VAT: 15.00%" in msg
    assert "Recent Updates:" in msg
    assert "Item A: ৳0 -> ৳100" in msg
    
    print("\n--- Test Passed Successfully! ---")
    
    # Cleanup (Optional, but good for test db)
    # tx.delete()

if __name__ == "__main__":
    try:
        test_whatsapp_message()
    except Exception as e:
        print(f"Error during test: {e}")
        import traceback
        traceback.print_exc()
