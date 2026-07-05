import uuid
from django.db import models
from django.utils import timezone

class SyncableModel(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, null=True, blank=True, db_index=True)
    is_dirty = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        skip_dirty = kwargs.pop('skip_dirty', False)
        if not skip_dirty:
            self.is_dirty = True
        super().save(*args, **kwargs)

class TransactionCategory(SyncableModel):
    name = models.CharField(max_length=100, unique=True)
    default_vat = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    default_tax = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    default_duty = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    default_service_charge = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    default_profit_margin = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    invoice_prefix = models.CharField(max_length=10, default="INV")
    
    default_supplier = models.ForeignKey('BusinessParty', on_delete=models.SET_NULL, null=True, blank=True, related_name='def_categories_supplier')
    default_buyer = models.ForeignKey('BusinessParty', on_delete=models.SET_NULL, null=True, blank=True, related_name='def_categories_buyer')
    
    COLOR_CHOICES = [
        ('gray', 'Gray (Default)'),
        ('blue', 'Blue'),
        ('purple', 'Purple'),
        ('green', 'Green'),
        ('red', 'Red'),
        ('yellow', 'Yellow'),
        ('indigo', 'Indigo'),
        ('pink', 'Pink'),
        ('orange', 'Orange'),
        ('teal', 'Teal'),
    ]
    color_theme = models.CharField(max_length=20, choices=COLOR_CHOICES, default='gray')
    
    def __str__(self):
        return self.name

    @property
    def is_lunch(self):
        name = self.name.lower()
        return any(keyword in name for keyword in ['tiffin', 'dinner', 'iftar', 'seheri', 'lunch'])

    @property
    def is_room_reservation(self):
        name = self.name.lower()
        return 'room' in name or 'hotel' in name or 'resort' in name or 'stay' in name

class Transaction(SyncableModel):
    STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('PRICED', 'Priced'),
        ('APPROVED', 'Approved'),
        ('SUBMITTED', 'Submitted'),
        ('RELEASED', 'Released'),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    
    supplier_name = models.CharField(max_length=255, blank=True)
    supplier_bin = models.CharField(max_length=100, blank=True)
    supplier_address = models.TextField(blank=True)
    supplier_contact = models.CharField(max_length=100, blank=True)

    buyer_name = models.CharField(max_length=255, blank=True)
    buyer_bin = models.CharField(max_length=100, blank=True)
    buyer_address = models.TextField(blank=True)
    buyer_contact = models.CharField(max_length=100, blank=True)

    vat_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=15.00)
    duty_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=5.00)
    tax_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    service_charge_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    profit_margin = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)

    secondary_vat_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=15.00)
    secondary_duty_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=5.00)
    secondary_tax_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    secondary_service_charge_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    secondary_profit_margin = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)

    transaction_category = models.ForeignKey(TransactionCategory, on_delete=models.SET_NULL, null=True, blank=True)
    secondary_category = models.ForeignKey(TransactionCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='secondary_transactions', help_text="Optional secondary category for dual-category registration.")
    
    invoice_number = models.CharField(max_length=100, blank=True, null=True)
    challan_number = models.CharField(max_length=100, blank=True, null=True)
    is_daily_basis = models.BooleanField(default=False, help_text="Toggles daily basis mode for room service / hotel stay bills.")
    
    creator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    
    created_at = models.DateTimeField(default=timezone.now)
    status_updated_at = models.DateTimeField(null=True, blank=True)
    
    @property
    def supplier_party(self):
        if not self.supplier_name:
            return None
        
        def clean_name(s):
            return ''.join(c for c in s.lower() if c.isalnum())
            
        target = clean_name(self.supplier_name)
        for party in BusinessParty.objects.filter(party_type='SUPPLIER'):
            if clean_name(party.name) == target:
                return party
        return None

    @property
    def display_subtotal(self):
        return sum(item.billed_total for item in self.items.all())

    @property
    def total_service_charge(self):
        import decimal
        primary_sub = sum(item.billed_total for item in self.items.filter(is_secondary=False))
        secondary_sub = sum(item.billed_total for item in self.items.filter(is_secondary=True))
        primary_sc = primary_sub * (self.service_charge_percentage / decimal.Decimal('100.0'))
        secondary_sc = secondary_sub * (self.secondary_service_charge_percentage / decimal.Decimal('100.0'))
        return round(primary_sc + secondary_sc, 3)

    @property
    def total_duty(self):
        return sum(item.duty_amount for item in self.items.all()) + self.service_charge_duty

    @property
    def total_tax(self):
        return sum(item.tax_amount for item in self.items.all()) + self.service_charge_tax

    @property
    def total_vat(self):
        return sum(item.vat_amount for item in self.items.all()) + self.service_charge_vat

    @property
    def grand_subtotal(self):
        return self.display_subtotal + self.total_service_charge

    @property
    def service_charge_duty(self):
        import decimal
        primary_sub = sum(item.billed_total for item in self.items.filter(is_secondary=False))
        secondary_sub = sum(item.billed_total for item in self.items.filter(is_secondary=True))
        primary_sc = primary_sub * (self.service_charge_percentage / decimal.Decimal('100.0'))
        secondary_sc = secondary_sub * (self.secondary_service_charge_percentage / decimal.Decimal('100.0'))
        primary_sc_duty = primary_sc * (self.duty_percentage / decimal.Decimal('100.0'))
        secondary_sc_duty = secondary_sc * (self.secondary_duty_percentage / decimal.Decimal('100.0'))
        return round(primary_sc_duty + secondary_sc_duty, 3)

    @property
    def service_charge_tax(self):
        import decimal
        primary_sub = sum(item.billed_total for item in self.items.filter(is_secondary=False))
        secondary_sub = sum(item.billed_total for item in self.items.filter(is_secondary=True))
        primary_sc = primary_sub * (self.service_charge_percentage / decimal.Decimal('100.0'))
        secondary_sc = secondary_sub * (self.secondary_service_charge_percentage / decimal.Decimal('100.0'))
        
        primary_sc_tax = decimal.Decimal('0.000')
        if self.tax_percentage > 0:
            gross = (primary_sc * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - self.tax_percentage)
            primary_sc_tax = gross - primary_sc
            
        secondary_sc_tax = decimal.Decimal('0.000')
        if self.secondary_tax_percentage > 0:
            gross = (secondary_sc * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - self.secondary_tax_percentage)
            secondary_sc_tax = gross - secondary_sc
            
        return round(primary_sc_tax + secondary_sc_tax, 3)

    @property
    def service_charge_vat(self):
        import decimal
        primary_sub = sum(item.billed_total for item in self.items.filter(is_secondary=False))
        secondary_sub = sum(item.billed_total for item in self.items.filter(is_secondary=True))
        primary_sc = primary_sub * (self.service_charge_percentage / decimal.Decimal('100.0'))
        secondary_sc = secondary_sub * (self.secondary_service_charge_percentage / decimal.Decimal('100.0'))
        
        primary_sc_duty = primary_sc * (self.duty_percentage / decimal.Decimal('100.0'))
        secondary_sc_duty = secondary_sc * (self.secondary_duty_percentage / decimal.Decimal('100.0'))
        
        primary_sc_tax = decimal.Decimal('0.000')
        if self.tax_percentage > 0:
            gross = (primary_sc * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - self.tax_percentage)
            primary_sc_tax = gross - primary_sc
            
        secondary_sc_tax = decimal.Decimal('0.000')
        if self.secondary_tax_percentage > 0:
            gross = (secondary_sc * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - self.secondary_tax_percentage)
            secondary_sc_tax = gross - secondary_sc
            
        primary_sc_vat = (primary_sc + primary_sc_duty + primary_sc_tax) * (self.vat_percentage / decimal.Decimal('100.0'))
        secondary_sc_vat = (secondary_sc + secondary_sc_duty + secondary_sc_tax) * (self.secondary_vat_percentage / decimal.Decimal('100.0'))
        
        return round(primary_sc_vat + secondary_sc_vat, 3)

    @property
    def service_charge_total_with_tax(self):
        return self.total_service_charge + self.service_charge_duty + self.service_charge_vat + self.service_charge_tax

    @property
    def grand_total(self):
        return self.display_subtotal + self.total_service_charge + self.total_duty + self.total_vat + self.total_tax

    @property
    def primary_total_service_charge(self):
        import decimal
        sub = sum(item.billed_total for item in self.items.filter(is_secondary=False))
        return round(sub * (self.service_charge_percentage / decimal.Decimal('100.0')), 3)

    @property
    def secondary_total_service_charge(self):
        import decimal
        sub = sum(item.billed_total for item in self.items.filter(is_secondary=True))
        return round(sub * (self.secondary_service_charge_percentage / decimal.Decimal('100.0')), 3)

    @property
    def primary_total_duty(self):
        import decimal
        item_duty = sum(item.duty_amount for item in self.items.filter(is_secondary=False))
        sc_duty = self.primary_total_service_charge * (self.duty_percentage / decimal.Decimal('100.0'))
        return round(item_duty + sc_duty, 3)

    @property
    def secondary_total_duty(self):
        import decimal
        item_duty = sum(item.duty_amount for item in self.items.filter(is_secondary=True))
        sc_duty = self.secondary_total_service_charge * (self.secondary_duty_percentage / decimal.Decimal('100.0'))
        return round(item_duty + sc_duty, 3)

    @property
    def primary_total_tax(self):
        import decimal
        item_tax = sum(item.tax_amount for item in self.items.filter(is_secondary=False))
        sc = self.primary_total_service_charge
        sc_tax = decimal.Decimal('0.000')
        if self.tax_percentage > 0:
            gross = (sc * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - self.tax_percentage)
            sc_tax = gross - sc
        return round(item_tax + sc_tax, 3)

    @property
    def secondary_total_tax(self):
        import decimal
        item_tax = sum(item.tax_amount for item in self.items.filter(is_secondary=True))
        sc = self.secondary_total_service_charge
        sc_tax = decimal.Decimal('0.000')
        if self.secondary_tax_percentage > 0:
            gross = (sc * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - self.secondary_tax_percentage)
            sc_tax = gross - sc
        return round(item_tax + sc_tax, 3)

    @property
    def primary_total_vat(self):
        import decimal
        item_vat = sum(item.vat_amount for item in self.items.filter(is_secondary=False))
        sc = self.primary_total_service_charge
        sc_duty = sc * (self.duty_percentage / decimal.Decimal('100.0'))
        sc_tax = decimal.Decimal('0.000')
        if self.tax_percentage > 0:
            gross = (sc * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - self.tax_percentage)
            sc_tax = gross - sc
        sc_vat = (sc + sc_duty + sc_tax) * (self.vat_percentage / decimal.Decimal('100.0'))
        return round(item_vat + sc_vat, 3)

    @property
    def secondary_total_vat(self):
        import decimal
        item_vat = sum(item.vat_amount for item in self.items.filter(is_secondary=True))
        sc = self.secondary_total_service_charge
        sc_duty = sc * (self.secondary_duty_percentage / decimal.Decimal('100.0'))
        sc_tax = decimal.Decimal('0.000')
        if self.secondary_tax_percentage > 0:
            gross = (sc * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - self.secondary_tax_percentage)
            sc_tax = gross - sc
        sc_vat = (sc + sc_duty + sc_tax) * (self.secondary_vat_percentage / decimal.Decimal('100.0'))
        return round(item_vat + sc_vat, 3)

    @property
    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def total_base_cost(self):
        return sum(item.base_price * item.quantity for item in self.items.all())

    @property
    def total_gross_profit(self):
        # Gross Profit = (Total Billed to buyer - Total Base Cost paid to supplier) - (Taxes/Duties/VAT collected for gov)
        # Simplified: It's the sum of your markup (Profit Margin) + Service Charge income.
        return (self.grand_total - self.total_base_cost) - (self.total_duty + self.total_tax + self.total_vat)

    def _grouped_lunch_items_for_sec(self, is_secondary):
        grouped = {}
        ungrouped = []
        for item in self.items.filter(is_secondary=is_secondary):
            if not item.entry_date:
                ungrouped.append({
                    'description': item.description or 'Lunch Supply',
                    'unit': item.unit or 'Pcs',
                    'quantity': item.quantity,
                    'billed_total': item.billed_total,
                    'total_with_tax': item.total_with_tax,
                    'billed_unit_price': item.billed_unit_price,
                })
                continue
            if item.entry_date not in grouped:
                desc = item.description if item.description else f"Lunch Supply ({item.entry_date.strftime('%d %b %Y')})"
                grouped[item.entry_date] = {
                    'description': desc,
                    'unit': item.unit or 'Day',
                    'quantity': 0,
                    'billed_total': 0,
                    'total_with_tax': 0,
                }
            grouped[item.entry_date]['quantity'] = 1
            grouped[item.entry_date]['billed_total'] += item.billed_total
            grouped[item.entry_date]['total_with_tax'] += item.total_with_tax
            
        result = []
        for date_key in sorted(grouped.keys()):
            g = grouped[date_key]
            g['billed_unit_price'] = g['billed_total']
            result.append(g)
        return result + ungrouped

    @property
    def grouped_primary_lunch_items(self):
        return self._grouped_lunch_items_for_sec(is_secondary=False)

    @property
    def grouped_secondary_lunch_items(self):
        return self._grouped_lunch_items_for_sec(is_secondary=True)

    @property
    def primary_items(self):
        return self.items.filter(is_secondary=False)

    @property
    def secondary_items(self):
        return self.items.filter(is_secondary=True)

    @property
    def grouped_lunch_items(self):
        return self._grouped_lunch_items_for_sec(is_secondary=False)

    def get_whatsapp_message(self):
        """Generates a text summary for WhatsApp notification."""
        items = self.items.all()
        item_list = []
        blank_items = []
        
        for item in items:
            desc = item.description or "Unnamed Item"
            qty = item.quantity
            # Added unit base price as requested
            item_list.append(f"• {desc} (x{qty}) - Base: BDT {item.base_price}")
            if not item.base_price or item.base_price == 0:
                blank_items.append(desc)
        
        msg = f"*Quotation Alert: {self.transaction_category.name}*\n"
        msg += f"Status: {self.status}\n"
        if self.invoice_number:
            msg += f"ID: {self.invoice_number}\n"
        msg += f"Date: {self.created_at.strftime('%d %b %Y')}\n\n"
        
        msg += "*Items:*\n" + "\n".join(item_list) + "\n\n"

        # Added Financial Details as requested
        msg += "*Financial Details:*\n"
        msg += f"• Profit Margin: {self.profit_margin}%\n"
        msg += f"• VAT: {self.vat_percentage}%\n"
        msg += f"• Tax: {self.tax_percentage}%\n"
        msg += f"• Duty: {self.duty_percentage}%\n"
        msg += f"• Service Charge: {self.service_charge_percentage}%\n\n"
        
        # Added Recent Changes as requested
        try:
            from audit.models import AuditLog
            last_change = self.audit_logs.filter(
                action__in=["Updated Prices", "Updated Transaction", "Saved Draft"]
            ).order_by('-timestamp').first()
            if last_change and ("Edits:" in last_change.details or "Modifications:" in last_change.details):
                details = last_change.details
                updates = ""
                if "Edits:" in details:
                    updates = details.split("Edits:")[1].strip()
                elif "Modifications:" in details:
                    updates = details.split("Modifications:")[1].strip()
                
                if updates:
                    msg += "*Recent Updates:*\n"
                    # Clean up the string to be more readable
                    updates_formatted = updates.replace(" | ", "\n").replace(", ", "\n")
                    msg += updates_formatted + "\n\n"
        except Exception:
            pass # Fail gracefully if audit lookup fails

        if blank_items:
            msg += "⚠️ *Action Required:* Pricing is missing for:\n"
            msg += ", ".join(blank_items) + "\n"
        else:
            msg += f"*Total Estimate: BDT {self.grand_total}*\n"
            
        return msg

    def get_whatsapp_url(self, request, target_phone=None):
        """Constructs the wa.me URL with pre-filled message."""
        import urllib.parse
        base_msg = self.get_whatsapp_message()
        
        # Determine deep link based on status
        current_domain = request.build_absolute_uri('/')[:-1]
        link = ""
        if self.status == 'DRAFT':
            # Link to pricing view if Accountant/Supplier needs to act
            link = f"{current_domain}/bills/{self.id}/pricing/" if self.transaction_category.name.lower() != 'lunch' else f"{current_domain}/bills/{self.id}/edit/"
        elif self.status == 'PRICED':
            # Link to approval view for Owner
            link = f"{current_domain}/bills/{self.id}/approve/"
        else:
            # General link to view
            link = f"{current_domain}/bills/{self.id}/print/quotation/"
            
        full_msg = f"{base_msg}\n🔗 View/Process: {link}"
        encoded_msg = urllib.parse.quote(full_msg)
        
        phone = target_phone or ""
        # Clean phone (ensure it has 880 if it starts with 0)
        phone = "".join(filter(str.isdigit, str(phone)))
        if phone.startswith('0') and len(phone) == 11:
            phone = '88' + phone
        elif len(phone) == 10: # case for 17...
            phone = '880' + phone
        
        return f"https://wa.me/{phone}?text={encoded_msg}"

    def __str__(self):
        return f"{self.invoice_number or 'Trans'} - {self.status}"

class TransactionItem(SyncableModel):
    transaction = models.ForeignKey(Transaction, related_name='items', on_delete=models.CASCADE)
    description = models.CharField(max_length=255, blank=True)
    unit = models.CharField(max_length=50, default='Pcs')
    quantity = models.IntegerField(default=1)
    base_price = models.DecimalField(max_digits=10, decimal_places=3, default=0.000)
    unit_price_uplifted = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, help_text="Owner set price before taxes. If blank, global profit margin applies.")

    sort_order = models.IntegerField(default=0)
    # Lunch Supply fields
    entry_date = models.DateField(null=True, blank=True)
    checkout_date = models.DateField(null=True, blank=True)
    restaurant_name = models.CharField(max_length=255, blank=True)
    is_secondary = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['sort_order', 'id']

    @property
    def billed_unit_price(self):
        import decimal
        price_basis = self.unit_price_uplifted if self.unit_price_uplifted is not None else self.base_price
        margin = self.transaction.secondary_profit_margin if self.is_secondary else self.transaction.profit_margin
        profit_pct = margin / decimal.Decimal('100.0')
        val = price_basis * (1 + profit_pct)
        return round(val, 3)

    @property
    def unit_profit_amount(self):
        return self.billed_unit_price - self.base_price

    @property
    def unit_uplifted_price(self):
        return self.billed_unit_price

    @property
    def unit_billed_total(self):
        import decimal
        if self.quantity == 0: return self.total_with_tax
        return self.total_with_tax / decimal.Decimal(str(self.quantity))

    def get_master_product(self):
        cat = self.transaction.secondary_category if (self.is_secondary and self.transaction.secondary_category) else self.transaction.transaction_category
        if not cat:
            return None
            
        # Determine the logical product name for lookup
        product_name = self.description
        
        # For lunch/tiffin, restaurant_name is a better identifier than the date-description
        if cat.is_lunch:
            if self.restaurant_name:
                product_name = self.restaurant_name
            else:
                import re
                if re.match(r'\d{4}-\d{2}-\d{2}', str(self.description)):
                    product_name = cat.name
        
        if not product_name or str(product_name).strip() == "":
            return None

        return Product.objects.filter(
            name=product_name,
            category=cat
        ).first()

    @property
    def master_base_price(self):
        prod = self.get_master_product()
        return prod.base_price if prod else None

    @property
    def master_upscale_value(self):
        prod = self.get_master_product()
        return prod.upscale_value if prod else None

    @property
    def billed_total(self):
        return self.quantity * self.billed_unit_price

    @property
    def duty_amount(self):
        import decimal
        duty_pct = self.transaction.secondary_duty_percentage if self.is_secondary else self.transaction.duty_percentage
        return round(self.billed_total * (duty_pct / decimal.Decimal('100.0')), 3)
        
    @property
    def tax_amount(self):
        import decimal
        tax_pct = self.transaction.secondary_tax_percentage if self.is_secondary else self.transaction.tax_percentage
        if tax_pct == 0:
            return decimal.Decimal('0.000')
        gross = (self.billed_total * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - tax_pct)
        return round(gross - self.billed_total, 3)

    @property
    def vat_amount(self):
        import decimal
        vat_pct = self.transaction.secondary_vat_percentage if self.is_secondary else self.transaction.vat_percentage
        return round((self.billed_total + self.duty_amount + self.tax_amount) * (vat_pct / decimal.Decimal('100.0')), 3)

    @property
    def total_with_tax(self):
        return self.billed_total + self.duty_amount + self.vat_amount + self.tax_amount

    def __str__(self):
        return self.description

class BusinessParty(SyncableModel):
    PARTY_TYPES = [
        ('SUPPLIER', 'Supplier'),
        ('BUYER', 'Buyer'),
    ]
    party_type = models.CharField(max_length=20, choices=PARTY_TYPES)
    name = models.CharField(max_length=255)
    ceo_name = models.CharField(max_length=255, blank=True, help_text="Name of the person in charge / CEO")
    bin_number = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    contact = models.CharField(max_length=100, blank=True)
    whatsapp_number = models.CharField(max_length=20, blank=True, help_text="e.g. 88017...")
    
    signature_image = models.ImageField(upload_to='signatures/', blank=True, null=True)
    seal_image = models.ImageField(upload_to='seals/', blank=True, null=True)
    
    def __str__(self):
        return self.name

class Product(SyncableModel):
    name = models.CharField(max_length=255)
    category = models.ForeignKey(TransactionCategory, on_delete=models.CASCADE, related_name='products')
    base_price = models.DecimalField(max_digits=12, decimal_places=3, default=0.000)
    upscale_value = models.DecimalField(max_digits=12, decimal_places=3, default=0.000)

    unit = models.CharField(max_length=50, default='Pcs')
    added_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('name', 'category')
        ordering = ['name']

    @property
    def price_diff(self):
        # Calculate the gap between owner's upscale value and actual supplier cost
        return self.upscale_value - self.base_price

    def __str__(self):
        return f"{self.name} ({self.category.name})"

class VisualRequisition(SyncableModel):
    uploader = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='uploaded_requisitions')
    image = models.ImageField(upload_to='requisitions/%Y/%m/')
    description = models.TextField(blank=True)
    authorized_users = models.ManyToManyField('accounts.User', related_name='authorized_requisitions', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Requisition by {self.uploader.username} - {self.created_at.strftime('%Y-%m-%d')}"

class CheckAuthorization(SyncableModel):
    reference_number = models.CharField(max_length=50, unique=True)
    issuer_name = models.CharField(max_length=255)
    issuer_address = models.TextField()
    trading_name = models.CharField(max_length=255, default="NRS Software")
    
    # The user who is authorized to collect the check
    nominee = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='received_authorizations')
    nominee_nid = models.CharField(max_length=50) # Snapshotted NID at time of issue
    
    check_number = models.CharField(max_length=100, blank=True, null=True)
    check_amount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    purpose = models.CharField(max_length=255, blank=True, null=True)
    reason = models.TextField(blank=True, null=True)
    
    valid_from = models.DateField()
    valid_to = models.DateField()
    
    created_at = models.DateTimeField(auto_now_add=True)
    authorized_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, related_name='issued_authorizations')

    def __str__(self):
        return f"{self.reference_number} - {self.nominee.username}"
