from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from .models import Transaction, TransactionItem, TransactionCategory

class BillingPropertiesTest(TestCase):
    def setUp(self):
        self.category = TransactionCategory.objects.create(
            name="Test Category",
            default_vat=Decimal("15.00"),
            default_tax=Decimal("5.00"),
            default_duty=Decimal("10.00")
        )
        self.transaction = Transaction.objects.create(
            transaction_category=self.category,
            vat_percentage=Decimal("15.00"),
            duty_percentage=Decimal("10.00"),
            tax_percentage=Decimal("5.00"),
            service_charge_percentage=Decimal("2.00"),
            supplier_name="Supplier Alpha",
            buyer_name="Buyer Beta",
            created_at=timezone.now()
        )
        self.item = TransactionItem.objects.create(
            transaction=self.transaction,
            description="Item 1",
            unit="Pcs",
            quantity=Decimal("10.000"),
            base_price=Decimal("100.000"),
            is_secondary=False
        )

    def test_item_properties(self):
        # Base Billed total = quantity * billed_unit_price = 10 * 100 = 1000
        self.assertEqual(self.item.billed_total, Decimal("1000.000"))
        
        # Duty = billed_total * 10% = 100
        self.assertEqual(self.item.duty_amount, Decimal("100.000"))
        
        # Tax = (billed_total * 100) / (100 - 5) - billed_total = 100000 / 95 - 1000 = 1052.632 - 1000 = 52.632
        # Let's verify by calculating it manually:
        expected_tax = round((Decimal("1000.00") * Decimal("100.00") / Decimal("95.00")) - Decimal("1000.00"), 3)
        self.assertEqual(self.item.tax_amount, expected_tax)

        # Base + Tax = billed_total + duty_amount + tax_amount = 1000 + 100 + 52.632 = 1152.632
        expected_base_plus_tax = Decimal("1000.000") + Decimal("100.000") + expected_tax
        self.assertEqual(self.item.total_base_plus_tax, expected_base_plus_tax)

        # Unit Base + Tax = expected_base_plus_tax / 10 = 115.2632
        self.assertEqual(self.item.unit_base_plus_tax, expected_base_plus_tax / Decimal("10.000"))

        # unit_tax_amount = tax_amount / 10 = 52.632 / 10 = 5.2632
        self.assertEqual(self.item.unit_tax_amount, expected_tax / Decimal("10.000"))

        # unit_price_with_tax = billed_unit_price (100) + unit_tax_amount (5.2632) = 105.2632
        self.assertEqual(self.item.unit_price_with_tax, Decimal("100.000") + expected_tax / Decimal("10.000"))

        # total_with_tax_only = billed_total (1000) + tax_amount (52.632) = 1052.632
        self.assertEqual(self.item.total_with_tax_only, Decimal("1000.000") + expected_tax)

    def test_transaction_properties(self):
        # Service charge = 1000 * 2% = 20
        self.assertEqual(self.transaction.total_service_charge, Decimal("20.000"))
        
        # service_charge_duty = 20 * 10% = 2
        self.assertEqual(self.transaction.service_charge_duty, Decimal("2.000"))
        
        # service_charge_tax = (20 * 100) / 95 - 20 = 21.053 - 20 = 1.053
        expected_sc_tax = round((Decimal("20.00") * Decimal("100.00") / Decimal("95.00")) - Decimal("20.00"), 3)
        self.assertEqual(self.transaction.service_charge_tax, expected_sc_tax)

        # service_charge_base_plus_tax = service_charge + duty + tax = 20 + 2 + 1.053 = 23.053
        self.assertEqual(
            self.transaction.service_charge_base_plus_tax,
            self.transaction.total_service_charge + self.transaction.service_charge_duty + self.transaction.service_charge_tax
        )

        # service_charge_with_tax_only = service_charge + tax = 20 + 1.053 = 21.053
        self.assertEqual(self.transaction.service_charge_with_tax_only, Decimal("20.000") + expected_sc_tax)

        # grand_base_plus_tax = price_after_discount_base + total_duty + total_tax
        # Since discount is 0: price_after_discount_base = display_subtotal (1000) + total_service_charge (20) = 1020
        # total_duty = item_duty (100) + sc_duty (2) = 102
        # total_tax = item_tax (52.632) + sc_tax (1.053) = 53.685
        # grand_base_plus_tax = 1020 + 102 + 53.685 = 1175.685
        expected_grand_base_plus_tax = self.transaction.price_after_discount_base + self.transaction.total_duty + self.transaction.total_tax
        self.assertEqual(self.transaction.grand_base_plus_tax, expected_grand_base_plus_tax)

        # grand_total_with_tax_only = price_after_discount_base (1020) + total_tax (53.685) = 1073.685
        expected_tax = round((Decimal("1000.00") * Decimal("100.00") / Decimal("95.00")) - Decimal("1000.00"), 3)
        expected_total_tax = expected_tax + expected_sc_tax
        self.assertEqual(self.transaction.grand_total_with_tax_only, Decimal("1020.000") + expected_total_tax)
