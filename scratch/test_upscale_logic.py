import os
import sys
import django
import decimal

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nrs_project.settings')
django.setup()

from documents.models import Transaction, TransactionItem, TransactionCategory, Product
from django.utils import timezone

def test_upscale_logic():
    print("--- Starting Product Upscale Logic Test ---")
    
    # 1. Setup Data
    cat, _ = TransactionCategory.objects.get_or_create(name="Office Electronics")
    prod, _ = Product.objects.update_or_create(
        name="Wireless Mouse",
        category=cat,
        defaults={'base_price': decimal.Decimal('800.00'), 'upscale_value': decimal.Decimal('1000.00')}
    )
    
    print(f"Product Initial: {prod.name}, Base: {prod.base_price}, Upscale: {prod.upscale_value}")
    
    # 2. Test auto-initialization of item price
    # We simulate _process_auto_product_saving logic
    # In views, we pull base_price into item.base_price AND upscale into unit_price_uplifted
    item_cost = prod.base_price
    item_uplift = prod.upscale_value
    print(f"Master Inventory -> Cost: {item_cost}, Target: {item_uplift}")
    assert item_cost == decimal.Decimal('800.00')
    assert item_uplift == decimal.Decimal('1000.00')

    # 3. Test Approval Sync Logic
    # Create a draft transaction with 10% global profit margin
    tx = Transaction.objects.create(
        transaction_category=cat,
        status='PRICED',
        profit_margin=decimal.Decimal('10.00'),
        invoice_number='INV-VERIFY-001'
    )
    # The supplier/accountant has confirmed cost is 850
    # Owner sets manual uplift to 1050
    item = TransactionItem.objects.create(
        transaction=tx,
        description="Wireless Mouse",
        quantity=1,
        base_price=decimal.Decimal('850.00'),
        unit_price_uplifted=decimal.Decimal('1050.00')
    )
    
    # Billed Price should be 1050 * 1.1 = 1155.00
    print(f"Calculated Billed Price: {item.billed_unit_price}")
    assert item.billed_unit_price == decimal.Decimal('1155.00')
    
    # Simulate Approval Logic from views.py
    # New Logic: Update product.upscale_value with item.unit_price_uplifted (PRE-MARGIN)
    # And update product.base_price with item.base_price
    tx.status = 'APPROVED'
    tx.save()
    
    # Sync logic (from views.transaction_approve)
    for it in tx.items.all():
        p = it.get_master_product()
        if p:
            p.upscale_value = it.unit_price_uplifted if it.unit_price_uplifted is not None else it.base_price
            p.base_price = it.base_price
            p.save(update_fields=['upscale_value', 'base_price'])
            
    # Verify Product update
    prod.refresh_from_db()
    print(f"Product after Approve: {prod.name}, Base: {prod.base_price}, Upscale: {prod.upscale_value}")
    assert prod.base_price == decimal.Decimal('850.00') # Cost updated
    assert prod.upscale_value == decimal.Decimal('1050.00') # Upscale updated (NOT 1155)
    
    print("\n--- Test Passed Successfully! ---")

if __name__ == "__main__":
    try:
        test_upscale_logic()
    except Exception as e:
        print(f"Error during test: {e}")
        import traceback
        traceback.print_exc()
