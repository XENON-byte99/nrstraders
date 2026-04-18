from django.db import models

class TransactionCategory(models.Model):
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

class Transaction(models.Model):
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

    transaction_category = models.ForeignKey(TransactionCategory, on_delete=models.SET_NULL, null=True, blank=True)
    
    invoice_number = models.CharField(max_length=100, blank=True, null=True)
    challan_number = models.CharField(max_length=100, blank=True, null=True)
    
    creator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status_updated_at = models.DateTimeField(null=True, blank=True)
    
    @property
    def display_subtotal(self):
        return sum(item.billed_total for item in self.items.all())

    @property
    def total_service_charge(self):
        import decimal
        return round(self.display_subtotal * (self.service_charge_percentage / decimal.Decimal('100.0')), 2)

    @property
    def total_duty(self):
        import decimal
        base = self.display_subtotal + self.total_service_charge
        return round(base * (self.duty_percentage / decimal.Decimal('100.0')), 2)

    @property
    def total_tax(self):
        import decimal
        tax_pct = self.tax_percentage
        if tax_pct == 0:
            return decimal.Decimal('0.00')
        base = self.display_subtotal + self.total_service_charge
        gross = (base * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - tax_pct)
        return round(gross - base, 2)

    @property
    def total_vat(self):
        import decimal
        base = self.display_subtotal + self.total_service_charge
        return round((base + self.total_duty + self.total_tax) * (self.vat_percentage / decimal.Decimal('100.0')), 2)

    @property
    def grand_subtotal(self):
        return self.display_subtotal + self.total_service_charge

    @property
    def service_charge_duty(self):
        import decimal
        return round(self.total_service_charge * (self.duty_percentage / decimal.Decimal('100.0')), 2)

    @property
    def service_charge_tax(self):
        import decimal
        tax_pct = self.tax_percentage
        if tax_pct == 0:
            return decimal.Decimal('0.00')
        base = self.total_service_charge
        gross = (base * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - tax_pct)
        return round(gross - base, 2)

    @property
    def service_charge_vat(self):
        import decimal
        base = self.total_service_charge
        return round((base + self.service_charge_duty + self.service_charge_tax) * (self.vat_percentage / decimal.Decimal('100.0')), 2)

    @property
    def service_charge_total_with_tax(self):
        return self.total_service_charge + self.service_charge_duty + self.service_charge_vat + self.service_charge_tax

    @property
    def grand_total(self):
        return self.display_subtotal + self.total_service_charge + self.total_duty + self.total_vat + self.total_tax

    @property
    def total_base_cost(self):
        return sum(item.base_price * item.quantity for item in self.items.all())

    @property
    def total_gross_profit(self):
        # Gross Profit = (Total Billed to buyer - Total Base Cost paid to supplier) - (Taxes/Duties/VAT collected for gov)
        # Simplified: It's the sum of your markup (Profit Margin) + Service Charge income.
        return (self.grand_total - self.total_base_cost) - (self.total_duty + self.total_tax + self.total_vat)

    @property
    def grouped_lunch_items(self):
        grouped = {}
        for item in self.items.all():
            if not item.entry_date: continue
            if item.entry_date not in grouped:
                grouped[item.entry_date] = {
                    'description': f"Lunch Supply ({item.entry_date.strftime('%d %b %Y')})",
                    'unit': item.unit or 'Day',
                    'quantity': 0,
                    'billed_total': 0,
                }
            # For lunch, typically quantity is 1 per restaurant per day, but we sum it.
            # Actually, the requirement says "sum all the amount of that day and write in one day"
            # It might be better to just set quantity=1 for the day, and rate = sum
            grouped[item.entry_date]['quantity'] = 1  # One day=1 unit
            grouped[item.entry_date]['billed_total'] += item.billed_total
            
        result = []
        for date_key in sorted(grouped.keys()):
            g = grouped[date_key]
            g['billed_unit_price'] = g['billed_total']  # Since qty is 1, rate = total
            result.append(g)
        return result

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

class TransactionItem(models.Model):
    transaction = models.ForeignKey(Transaction, related_name='items', on_delete=models.CASCADE)
    description = models.CharField(max_length=255, blank=True)
    unit = models.CharField(max_length=50, default='Pcs')
    quantity = models.IntegerField(default=1)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    unit_price_uplifted = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Owner set price before taxes. If blank, global profit margin applies.")
    sort_order = models.IntegerField(default=0)
    # Lunch Supply fields
    entry_date = models.DateField(null=True, blank=True)
    restaurant_name = models.CharField(max_length=255, blank=True)
    
    class Meta:
        ordering = ['sort_order', 'id']
    
    @property
    def billed_unit_price(self):
        import decimal
        # Layer 1 & 2: Start with Manual Uplift if set, otherwise Supplier Base
        price_basis = self.unit_price_uplifted if self.unit_price_uplifted is not None else self.base_price
        # Layer 3: Apply Global Profit Margin on top
        profit_pct = self.transaction.profit_margin / decimal.Decimal('100.0')
        val = price_basis * (1 + profit_pct)
        return round(val, 2)

    @property
    def unit_profit_amount(self):
        return self.billed_unit_price - self.base_price

    @property
    def unit_uplifted_price(self):
        # User defined Uplifted Price is the price before VAT/Duty/Tax
        return self.billed_unit_price

    @property
    def unit_billed_total(self):
        # Final unit price charged to buyer (including everything)
        import decimal
        if self.quantity == 0: return self.total_with_tax
        return self.total_with_tax / decimal.Decimal(str(self.quantity))

    def get_master_product(self):
        """Helper to find the master catalog product for this item."""
        if not self.description or not self.transaction.transaction_category:
            return None
        return Product.objects.filter(
            name=self.description,
            category=self.transaction.transaction_category
        ).first()

    @property
    def master_base_price(self):
        """Standard supplier cost from the master product list."""
        prod = self.get_master_product()
        return prod.base_price if prod else None

    @property
    def master_upscale_value(self):
        """Owner's targeted base price from the master product list."""
        prod = self.get_master_product()
        return prod.upscale_value if prod else None

    @property
    def billed_total(self):
        return self.quantity * self.billed_unit_price

    @property
    def duty_amount(self):
        import decimal
        return round(self.billed_total * (self.transaction.duty_percentage / decimal.Decimal('100.0')), 2)
        
    @property
    def tax_amount(self):
        import decimal
        tax_pct = self.transaction.tax_percentage
        if tax_pct == 0:
            return decimal.Decimal('0.00')
        gross = (self.billed_total * decimal.Decimal('100.0')) / (decimal.Decimal('100.0') - tax_pct)
        return round(gross - self.billed_total, 2)

    @property
    def vat_amount(self):
        import decimal
        return round((self.billed_total + self.duty_amount + self.tax_amount) * (self.transaction.vat_percentage / decimal.Decimal('100.0')), 2)

    @property
    def total_with_tax(self):
        return self.billed_total + self.duty_amount + self.vat_amount + self.tax_amount

    def __str__(self):
        return self.description

class BusinessParty(models.Model):
    PARTY_TYPES = [
        ('SUPPLIER', 'Supplier'),
        ('BUYER', 'Buyer'),
    ]
    party_type = models.CharField(max_length=20, choices=PARTY_TYPES)
    name = models.CharField(max_length=255)
    bin_number = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    contact = models.CharField(max_length=100, blank=True)
    whatsapp_number = models.CharField(max_length=20, blank=True, help_text="e.g. 88017...")
    
    def __str__(self):
        return self.name

class Product(models.Model):
    name = models.CharField(max_length=255)
    category = models.ForeignKey(TransactionCategory, on_delete=models.CASCADE, related_name='products')
    base_price = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    upscale_value = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    unit = models.CharField(max_length=50, default='Pcs')
    added_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('name', 'category')
        ordering = ['name']

    @property
    def price_diff(self):
        # Calculate the gap between owner's upscale value and actual supplier cost
        return self.upscale_value - self.base_price

    def __str__(self):
        return f"{self.name} ({self.category.name})"

class VisualRequisition(models.Model):
    uploader = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='uploaded_requisitions')
    image = models.ImageField(upload_to='requisitions/%Y/%m/')
    description = models.TextField(blank=True)
    authorized_users = models.ManyToManyField('accounts.User', related_name='authorized_requisitions', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Requisition by {self.uploader.username} - {self.created_at.strftime('%Y-%m-%d')}"

class CheckAuthorization(models.Model):
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
