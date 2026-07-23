from django import forms
from .models import Transaction, TransactionItem, BusinessParty, TransactionCategory, Product, VisualRequisition, CheckAuthorization
from django.forms import inlineformset_factory

class TransactionForm(forms.ModelForm):
    transaction_category = forms.ModelChoiceField(
        queryset=TransactionCategory.objects.all(),
        empty_label="---------",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    secondary_category = forms.ModelChoiceField(
        queryset=TransactionCategory.objects.all(),
        required=False,
        empty_label="---------",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    class Meta:
        model = Transaction
        fields = [
            'transaction_category', 'secondary_category', 'invoice_number', 'challan_number', 'created_at',
            'supplier_name', 'supplier_bin', 'supplier_address', 'supplier_contact',
            'buyer_name', 'buyer_bin', 'buyer_address', 'buyer_contact',
            'vat_percentage', 'duty_percentage', 'tax_percentage', 'service_charge_percentage', 'profit_margin',
            'secondary_vat_percentage', 'secondary_duty_percentage', 'secondary_tax_percentage', 'secondary_service_charge_percentage', 'secondary_profit_margin',
            'discount_percentage', 'discount_flat',
            'is_daily_basis', 'decimal_display_mode', 'calculation_mode'
        ]
        widgets = {
            'created_at': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}, format='%Y-%m-%dT%H:%M'),
        }

class TransactionHeaderForm(forms.ModelForm):
    class Meta:
        model = Transaction
        # NOTE: is_daily_basis is intentionally NOT here.
        # It is edited on the create/edit form, not at approval.
        fields = [
            'invoice_number', 'challan_number', 'created_at',
            'supplier_name', 'supplier_bin', 'supplier_address', 'supplier_contact',
            'buyer_name', 'buyer_bin', 'buyer_address', 'buyer_contact',
            'decimal_display_mode', 'calculation_mode'
        ]
        widgets = {
            'created_at': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}, format='%Y-%m-%dT%H:%M'),
            'supplier_address': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
            'buyer_address': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
            'decimal_display_mode': forms.Select(attrs={'class': 'form-control'}),
            'calculation_mode': forms.RadioSelect(attrs={'class': 'w-4 h-4 text-indigo-600 focus:ring-indigo-500'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Safely set initials for supplier/buyer if they are empty
        if 'supplier_name' in self.fields:
            if not self.initial.get('supplier_name') and not getattr(self.instance, 'supplier_name', None):
                self.fields['supplier_name'].initial = "NRS Traders"
                self.fields['supplier_bin'].initial = "007212741-0305"
                self.fields['supplier_address'].initial = "Kadomtoli Adamjee Nagar, Siddhirganj , Narayanganj"
                self.fields['supplier_contact'].initial = "01XXXXXXXXX"

        if 'buyer_name' in self.fields:
            if not self.initial.get('buyer_name') and not getattr(self.instance, 'buyer_name', None):
                self.fields['buyer_name'].initial = "Lintas Bangladesh Co. LTD"
                self.fields['buyer_bin'].initial = "000150053-0305"
                self.fields['buyer_address'].initial = "Adamjee EPZ, Siddhirganj, Narayanganj, Dhaka"
                self.fields['buyer_contact'].initial = "01XXXXXXXXX"

# Small inputs for the collapsible per-item tax override in the create/edit form
_OVERRIDE_WIDGETS = {
    'override_vat': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '99.99', 'placeholder': 'VAT%', 'class': 'ov-vat'}),
    'override_tax': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '99.99', 'placeholder': 'TAX%', 'class': 'ov-tax'}),
}

class TransactionItemForm(forms.ModelForm):
    class Meta:
        model = TransactionItem
        fields = ['entry_date', 'checkout_date', 'restaurant_name', 'description', 'unit', 'quantity', 'base_price', 'sort_order', 'is_secondary', 'override_vat', 'override_tax']
        widgets = {
            'entry_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'checkout_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'quantity': forms.NumberInput(attrs={'step': 'any', 'min': '0.001'}),
            'base_price': forms.NumberInput(attrs={'step': '0.001'}),
            **_OVERRIDE_WIDGETS,
        }


TransactionItemFormSet = inlineformset_factory(
    Transaction, 
    TransactionItem, 
    form=TransactionItemForm, 
    extra=0, 
    can_delete=True
)

class SupplierPricingForm(forms.ModelForm):
    class Meta:
        model = TransactionItem
        fields = ['base_price']
        widgets = {
            'base_price': forms.NumberInput(attrs={'step': '0.001'}),
        }


class SupplierPricingFormSet(inlineformset_factory(
    Transaction, 
    TransactionItem, 
    form=SupplierPricingForm, 
    extra=0, 
    can_delete=False
)):
    pass

class ApprovalPricingForm(forms.ModelForm):
    class Meta:
        model = TransactionItem
        # override_vat/override_tax: per-item rates that beat the bill's
        # category defaults (blank = inherit).
        fields = ['entry_date', 'checkout_date', 'description', 'unit', 'quantity', 'base_price', 'unit_price_uplifted', 'sort_order', 'is_secondary', 'override_vat', 'override_tax']
        widgets = {
            'entry_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'checkout_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'quantity': forms.NumberInput(attrs={'step': 'any', 'min': '0.001'}),
            'base_price': forms.NumberInput(attrs={'step': '0.001'}),
            'unit_price_uplifted': forms.NumberInput(attrs={'step': '0.001'}),
        }


class ApprovalPricingFormSet(inlineformset_factory(
    Transaction,
    TransactionItem,
    form=ApprovalPricingForm,
    extra=0,
    can_delete=True
)):
    pass

class BusinessPartyForm(forms.ModelForm):
    class Meta:
        model = BusinessParty
        fields = ['party_type', 'name', 'ceo_name', 'designation', 'bin_number', 'address', 'contact', 'whatsapp_number', 'color_theme', 'print_layout', 'signature_image', 'seal_image']

class TransactionCategoryForm(forms.ModelForm):
    class Meta:
        model = TransactionCategory
        fields = ['name', 'invoice_prefix', 'color_theme', 'default_vat', 'default_tax', 'default_duty', 'default_service_charge', 'default_profit_margin', 'default_supplier', 'default_buyer']

# ── Lunch Supply Forms ─────────────────────────────────────────
class LunchItemForm(forms.ModelForm):
    entry_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        required=False,
        label="Date"
    )
    checkout_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        required=False,
        label="Checkout Date"
    )
    restaurant_name = forms.CharField(max_length=255, required=False)
    description = forms.CharField(max_length=255, required=False)
    unit = forms.CharField(
        max_length=50,
        required=False,
        initial='Meal',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Unit'})
    )
    base_price = forms.DecimalField(max_digits=10, decimal_places=3, initial=0)

    class Meta:
        model = TransactionItem
        fields = ['entry_date', 'checkout_date', 'restaurant_name', 'description', 'unit', 'quantity', 'base_price', 'sort_order', 'is_secondary', 'override_vat', 'override_tax']
        widgets = {
            'quantity': forms.NumberInput(attrs={'step': 'any', 'min': '0.001'}),
            **_OVERRIDE_WIDGETS,
        }

LunchItemFormSet = inlineformset_factory(
    Transaction,
    TransactionItem,
    form=LunchItemForm,
    extra=0,
    can_delete=True,
)

# ── Room Reservation Forms ─────────────────────────────────────────
class RoomItemForm(forms.ModelForm):
    entry_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        required=False,
        label="From Date"
    )
    checkout_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        required=False,
        label="To Date"
    )
    description = forms.CharField(max_length=255, required=False)
    unit = forms.CharField(
        max_length=50,
        required=False,
        initial='Day',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Unit'})
    )
    quantity = forms.DecimalField(
        required=False,
        initial=1,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Qty', 'style': 'text-align: center;', 'step': 'any', 'min': '0.001'})
    )
    base_price = forms.DecimalField(max_digits=10, decimal_places=3, initial=0)

    class Meta:
        model = TransactionItem
        fields = ['entry_date', 'checkout_date', 'restaurant_name', 'description', 'unit', 'quantity', 'base_price', 'sort_order', 'is_secondary', 'override_vat', 'override_tax']
        widgets = dict(_OVERRIDE_WIDGETS)

RoomItemFormSet = inlineformset_factory(
    Transaction,
    TransactionItem,
    form=RoomItemForm,
    extra=0,
    can_delete=True,
)

class LunchSupplierPricingForm(forms.ModelForm):
    """Supplier confirms/adjusts base_price for a lunch item."""
    class Meta:
        model = TransactionItem
        fields = ['base_price']
        widgets = {
            'base_price': forms.NumberInput(attrs={'step': '0.001'}),
        }


class LunchSupplierPricingFormSet(inlineformset_factory(
    Transaction,
    TransactionItem,
    form=LunchSupplierPricingForm,
    extra=0,
    can_delete=False,
)):
    pass

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'category', 'base_price', 'upscale_value', 'unit', 'is_approved']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full p-2 border rounded-lg', 'placeholder': 'Product Name'}),
            'category': forms.Select(attrs={'class': 'w-full p-2 border rounded-lg'}),
            'base_price': forms.NumberInput(attrs={'class': 'w-full p-2 border rounded-lg', 'placeholder': 'Buying Price', 'step': '0.001'}),
            'upscale_value': forms.NumberInput(attrs={'class': 'w-full p-2 border rounded-lg', 'placeholder': 'Selling Price', 'step': '0.001'}),

            'unit': forms.TextInput(attrs={'class': 'w-full p-2 border rounded-lg', 'placeholder': 'Pcs'}),
            'is_approved': forms.CheckboxInput(attrs={'class': 'rounded text-indigo-600 focus:ring-indigo-500'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        if self.user:
            is_owner = self.user.role == 'OWNER' or self.user.is_superuser
            if not is_owner:
                # Disable upscale_value and is_approved for non-owners
                if 'upscale_value' in self.fields:
                    self.fields['upscale_value'].widget.attrs['readonly'] = True
                    self.fields['upscale_value'].widget.attrs['class'] += ' bg-gray-100 cursor-not-allowed'
                    self.fields['upscale_value'].help_text = "Only the owner can set the selling price."
                
                if 'is_approved' in self.fields:
                    self.fields['is_approved'].disabled = True
                    self.fields['is_approved'].help_text = "Approval is managed by the owner."
        
        # Categorize layout if needed or just styling
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                if 'class' not in field.widget.attrs:
                    field.widget.attrs['class'] = 'w-full p-2 border rounded-lg'
                elif 'w-full' not in field.widget.attrs['class']:
                    field.widget.attrs['class'] += ' w-full p-2 border rounded-lg'

# ── Requisition Forms ─────────────────────────────────────────
class RequisitionForm(forms.ModelForm):
    transaction_category = forms.ModelChoiceField(
        queryset=TransactionCategory.objects.all(),
        empty_label="---------",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    secondary_category = forms.ModelChoiceField(
        queryset=TransactionCategory.objects.all(),
        required=False,
        empty_label="---------",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    class Meta:
        model = Transaction
        fields = ['transaction_category', 'secondary_category', 'buyer_name']
        widgets = {
            'buyer_name': forms.TextInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Always default to Lintas as requested, regardless of user
        if not self.instance.pk:
            self.fields['buyer_name'].initial = "Lintas Bangladesh Co. LTD"

class RequisitionItemForm(forms.ModelForm):
    class Meta:
        model = TransactionItem
        fields = ['description', 'unit', 'base_price', 'quantity', 'sort_order']
        widgets = {
            'quantity': forms.NumberInput(attrs={'step': 'any', 'min': '0.001'}),
            'base_price': forms.NumberInput(attrs={'step': '0.001'}),
        }

RequisitionItemFormSet = inlineformset_factory(
    Transaction,
    TransactionItem,
    form=RequisitionItemForm,
    extra=1,
    can_delete=True,
)

class VisualRequisitionForm(forms.ModelForm):
    class Meta:
        model = VisualRequisition
        fields = ['image', 'description', 'authorized_users']
        widgets = {
            'description': forms.Textarea(attrs={'class': 'w-full p-2 border rounded-xl', 'rows': 3, 'placeholder': 'Optional description...'}),
            'authorized_users': forms.CheckboxSelectMultiple(attrs={'class': 'space-y-2'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User
        # List all users EXCEPT the uploader if we know who they are, but for multi-select we list all active staff/partners
        self.fields['authorized_users'].queryset = User.objects.all().order_by('username')
        self.fields['authorized_users'].label_from_instance = lambda obj: f"{obj.username.title()} ({obj.role})"

class CheckAuthorizationForm(forms.ModelForm):
    issuer_select = forms.ModelChoiceField(
        queryset=BusinessParty.objects.filter(party_type='BUYER'),
        required=False,
        label="Quick Select Issuer",
        widget=forms.Select(attrs={'class': 'form-control', 'onchange': 'updateIssuer(this)'})
    )

    class Meta:
        model = CheckAuthorization
        fields = [
            'reference_number', 'issuer_name', 'issuer_address', 'trading_name',
            'nominee', 'nominee_nid', 'check_number', 'check_amount',
            'purpose', 'reason', 'valid_from', 'valid_to'
        ]
        widgets = {
            'valid_from': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'valid_to': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'issuer_address': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
            'reason': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
            'nominee': forms.Select(attrs={'class': 'form-control'}),
            'nominee_nid': forms.TextInput(attrs={'readonly': 'readonly', 'class': 'bg-gray-100'}),
            'reference_number': forms.TextInput(attrs={'readonly': 'readonly', 'class': 'bg-gray-100'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User
        self.fields['nominee'].queryset = User.objects.all().order_by('username')
        
        # Make check fields optional
        self.fields['check_number'].required = False
        self.fields['check_amount'].required = False
        self.fields['purpose'].required = False
        self.fields['reason'].required = False
        
        for field_name, field in self.fields.items():
            if 'class' not in field.widget.attrs:
                field.widget.attrs['class'] = 'form-control'
            
            # Make sure NID is updated if user is selected
            if field_name == 'nominee':
                field.widget.attrs['onchange'] = 'updateNID(this)'
