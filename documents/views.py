from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required, user_passes_test
from accounts.decorators import (accountant_required, supplier_required, 
                                 owner_or_accountant_required, buyer_required, 
                                 permission_required, owner_required)
from .models import Transaction, BusinessParty, TransactionCategory, TransactionItem, Product, VisualRequisition, CheckAuthorization
from .forms import (TransactionForm, TransactionItemFormSet, SupplierPricingFormSet,
                    LunchItemFormSet, LunchSupplierPricingFormSet, RoomItemFormSet,
                    BusinessPartyForm, TransactionCategoryForm, ProductForm,
                    RequisitionForm, RequisitionItemFormSet, VisualRequisitionForm)
from django.contrib import messages
from audit.utils import log_audit
from audit.models import AuditLog
from django.utils.dateparse import parse_date
from datetime import date
from .category_rules import rules_for_category, normalize_items

def _is_lunch_cat(cat):
    if not cat:
        return False
    return cat.is_lunch

def _is_room_cat(cat):
    if not cat:
        return False
    return cat.is_room_reservation

def _get_next_serial_number(category):
    from django.utils import timezone
    import re
    
    # Filter all transactions for this category
    txns = Transaction.objects.filter(
        transaction_category=category,
    ).only('invoice_number')
    
    max_seq = 0
    for t in txns:
        if t.invoice_number:
            match = re.search(r'-(\d+)$', t.invoice_number)
            if match:
                try:
                    seq = int(match.group(1))
                    if seq > max_seq: max_seq = seq
                except ValueError: continue
    
    return max_seq + 1

def _process_auto_product_saving(transaction, items, user=None):
    for item in items:
        # Resolve category for this item
        item_category = transaction.secondary_category if (item.is_secondary and transaction.secondary_category) else transaction.transaction_category
        if not item_category:
            continue
            
        # Determine the logical product name for lookup (same logic as in models.py)
        product_name = item.description
        if _is_lunch_cat(item_category):
            if item.restaurant_name:
                product_name = item.restaurant_name
            else:
                import re
                if re.match(r'\d{4}-\d{2}-\d{2}', str(item.description)):
                    product_name = item_category.name

        if not product_name or str(product_name).strip() == "":
            continue
        
        # Try to find matching product in this category
        product = Product.objects.filter(
            name__iexact=product_name,
            category=item_category
        ).first()
        
        if product:
            # If item has no price (like a new requisition), pull master values
            if item.base_price == 0:
                item.base_price = product.base_price
                if item.unit_price_uplifted is None:
                    if item_category.is_lunch or item_category.is_room_reservation:
                        item.unit_price_uplifted = item.base_price
                    else:
                        item.unit_price_uplifted = product.upscale_value
                item.save(update_fields=['base_price', 'unit_price_uplifted'])
        else:
            # If it's a new item WITH a price (from a full bill), create it as a new Product
            if item.base_price > 0:
                Product.objects.create(
                    name=product_name,
                    category=item_category,
                    base_price=item.base_price,    # Initial actual cost
                    upscale_value=item.base_price, # Initial upscale value (owner can edit later)
                    unit=item.unit or 'Pcs',
                    added_by=user,
                    is_approved=(user and (user.role == 'OWNER' or user.is_superuser))
                )

def _sync_transaction_category_defaults(transaction):
    if transaction.status in ['DRAFT', 'PRICED']:
        modified = False
        if transaction.transaction_category:
            cat = transaction.transaction_category
            if transaction.vat_percentage != cat.default_vat:
                transaction.vat_percentage = cat.default_vat
                modified = True
            if transaction.tax_percentage != cat.default_tax:
                transaction.tax_percentage = cat.default_tax
                modified = True
            if transaction.duty_percentage != cat.default_duty:
                transaction.duty_percentage = cat.default_duty
                modified = True
            if transaction.service_charge_percentage != cat.default_service_charge:
                transaction.service_charge_percentage = cat.default_service_charge
                modified = True
            if transaction.profit_margin != cat.default_profit_margin:
                transaction.profit_margin = cat.default_profit_margin
                modified = True
        
        if transaction.secondary_category:
            sec_cat = transaction.secondary_category
            if transaction.secondary_vat_percentage != sec_cat.default_vat:
                transaction.secondary_vat_percentage = sec_cat.default_vat
                modified = True
            if transaction.secondary_tax_percentage != sec_cat.default_tax:
                transaction.secondary_tax_percentage = sec_cat.default_tax
                modified = True
            if transaction.secondary_duty_percentage != sec_cat.default_duty:
                transaction.secondary_duty_percentage = sec_cat.default_duty
                modified = True
            if transaction.secondary_service_charge_percentage != sec_cat.default_service_charge:
                transaction.secondary_service_charge_percentage = sec_cat.default_service_charge
                modified = True
            if transaction.secondary_profit_margin != sec_cat.default_profit_margin:
                transaction.secondary_profit_margin = sec_cat.default_profit_margin
                modified = True
                
        if modified:
            transaction.save()

def _create_transaction_snapshot(transaction, user=None):
    from .models import TransactionSnapshot
    tx_data = {
        'id': transaction.id,
        'invoice_number': transaction.invoice_number,
        'status': transaction.status,
        'supplier_name': transaction.supplier_name,
        'buyer_name': transaction.buyer_name,
        'created_at': str(transaction.created_at),
        'grand_total': str(transaction.grand_total),
        'items': []
    }
    for item in transaction.items.all():
        tx_data['items'].append({
            'id': item.id,
            'description': item.description,
            'quantity': float(item.quantity) if item.quantity is not None else 0.0,
            'unit': item.unit,
            'base_price': str(item.base_price),
            'billed_total': str(item.billed_total)
        })
    TransactionSnapshot.objects.create(
        transaction=transaction,
        created_by=user if user and user.is_authenticated else None,
        snapshot_data=tx_data
    )

@login_required
def home(request):
    return redirect('transaction_list')

@login_required
def transaction_list(request):
    from django.db.models import Q
    transactions = Transaction.objects.select_related(
        'transaction_category', 'secondary_category'
    ).prefetch_related('items')

    # ── Filters ──
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    category_id = request.GET.get('category', '').strip()
    date_from = parse_date(request.GET.get('date_from', '')) if request.GET.get('date_from') else None
    date_to = parse_date(request.GET.get('date_to', '')) if request.GET.get('date_to') else None

    if q:
        transactions = transactions.filter(
            Q(invoice_number__icontains=q) |
            Q(challan_number__icontains=q) |
            Q(supplier_name__icontains=q) |
            Q(buyer_name__icontains=q) |
            Q(items__description__icontains=q)
        ).distinct()
    if status:
        transactions = transactions.filter(status=status)
    if category_id:
        transactions = transactions.filter(
            Q(transaction_category_id=category_id) | Q(secondary_category_id=category_id)
        )
    if date_from:
        transactions = transactions.filter(created_at__date__gte=date_from)
    if date_to:
        transactions = transactions.filter(created_at__date__lte=date_to)

    transactions = transactions.order_by('-created_at')

    filters_active = bool(q or status or category_id or date_from or date_to)
    context = {
        'transactions': transactions,
        'categories': TransactionCategory.objects.order_by('name'),
        'status_choices': Transaction.STATUS_CHOICES,
        'filter_q': q,
        'filter_status': status,
        'filter_category': category_id,
        'filter_date_from': request.GET.get('date_from', ''),
        'filter_date_to': request.GET.get('date_to', ''),
        'filters_active': filters_active,
        'result_count': transactions.count(),
    }
    return render(request, 'documents/transaction_list.html', context)

@login_required
@permission_required('p_create_bill')
def transaction_create(request):
    if request.method == 'POST':
        form = TransactionForm(request.POST)
        cat_id = request.POST.get('transaction_category')
        try:
            cat_obj = TransactionCategory.objects.get(pk=cat_id) if cat_id else None
        except TransactionCategory.DoesNotExist:
            cat_obj = None
        # Category logic is isolated in category_rules.py — the primary category
        # picks the formset; each item is normalized by its own category's rules.
        FormSetClass = rules_for_category(cat_obj).entry_formset()
        formset = FormSetClass(request.POST)
        if form.is_valid() and formset.is_valid():
            transaction = form.save(commit=False)
            transaction.status = 'DRAFT'
            transaction.save()
            items = formset.save(commit=False)
            for item in items:
                item.transaction = transaction
            normalize_items(transaction, items)
            for item in items:
                item.save()
            for obj in formset.deleted_objects:
                obj.delete()
            
            # Auto-save products
            _process_auto_product_saving(transaction, items, user=request.user)

            cat_names = cat_obj.name if cat_obj else 'Uncategorized'
            if transaction.secondary_category:
                cat_names += f" & {transaction.secondary_category.name}"
            audit_details = f"Created new DRAFT Bill #{transaction.invoice_number} under category {cat_names}. Initialized with {len(items)} line item(s)."
            log_audit(request.user, "Created Transaction", audit_details, transaction=transaction)

            messages.success(request, 'Transaction created successfully.')
            return redirect('transaction_list')
        else:
            for field, errors in form.errors.items():
                messages.error(request, f"{field.replace('_', ' ').title()}: {', '.join(errors)}")
            for i, f in enumerate(formset.forms):
                for field, errors in f.errors.items():
                    messages.error(request, f"Row {i+1} ({field.replace('_', ' ').title()}): {', '.join(errors)}")
            if formset.non_form_errors():
                messages.error(request, f"Formset error: {', '.join(formset.non_form_errors())}")
    else:

        form = TransactionForm()
        formset = TransactionItemFormSet()
    
    is_lunch = _is_lunch_cat(getattr(form.instance, 'transaction_category', None)) if form.instance.pk else False
    is_room = _is_room_cat(getattr(form.instance, 'transaction_category', None)) if form.instance.pk else False
    parties = BusinessParty.objects.all()
    # Unified entry form: every category uses the one item table (normal mode).
    return render(request, 'documents/transaction_form.html', {
        'form': form, 'formset': formset, 'parties': parties, 'is_lunch': False, 'is_room': False
    })

@login_required
@permission_required('p_price_bill')
def transaction_pricing(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk)
    # _sync_transaction_category_defaults(transaction) # DISABLED: This was overwriting user inputs with category defaults
    
    # Immutability Check: Only Owners/Admins can edit after DRAFT
    is_admin = request.user.role == 'OWNER' or request.user.is_superuser
    if not is_admin and transaction.status != 'DRAFT':
        messages.error(request, "Access Denied: Pricing can only be managed while the bill is in DRAFT status.")
        return redirect('transaction_list')

    cat_name = transaction.transaction_category.name.lower() if transaction.transaction_category else ""
    is_lunch = _is_lunch_cat(transaction.transaction_category)
    is_room = _is_room_cat(transaction.transaction_category)
    is_meal = is_lunch or is_room

    # Category logic is isolated in category_rules.py.
    rules = rules_for_category(transaction.transaction_category)
    FormSetClass = rules.pricing_formset()

    if request.method == 'POST':
        formset = FormSetClass(request.POST, instance=transaction)
        if formset.is_valid():
            # Audit wording is provided per-category by the rules object.
            changes = []
            for item_form in formset.forms:
                if item_form.has_changed():
                    note = rules.pricing_change_note(item_form)
                    if note:
                        changes.append(note)
            if formset.deleted_forms:
                changes.append("Removed items")

            # Save + normalize each item by its own category's rules.
            items = formset.save(commit=False)
            normalize_items(transaction, items)
            for item in items:
                item.save()
            for obj in formset.deleted_objects:
                obj.delete()

            if is_meal and 'save_draft' in request.POST:
                transaction.status = 'DRAFT'
                msg = 'Draft saved successfully.'
                audit_action = "Saved Draft"
                details = f"Saved Bill #{transaction.invoice_number} in DRAFT state. Logged {len(formset.forms)} priced entries."

            else:
                transaction.status = 'PRICED'
                msg = 'Pricing submitted successfully.'
                audit_action = "Updated Prices"
                details = f"Finalized supplier rates for Bill #{transaction.invoice_number}. State updated to PRICED."


            transaction.save()
            if changes:
                details += " Edits: " + ", ".join(changes)
            log_audit(request.user, audit_action, details, transaction=transaction)
            messages.success(request, msg)
            return redirect('transaction_list')
    else:
        formset = FormSetClass(instance=transaction)

    if request.method == 'POST' and not formset.is_valid():
        for i, form in enumerate(formset.forms):
            for field, errors in form.errors.items():
                messages.error(request, f"Row {i+1} ({field}): {', '.join(errors)}")
        if formset.non_form_errors():
            messages.error(request, f"Form error: {', '.join(formset.non_form_errors())}")

    # Unified pricing: every category uses the one price-only table (general branch).
    return render(request, 'documents/transaction_pricing.html', {
        'transaction': transaction, 'formset': formset, 'is_meal': False, 'is_lunch': False, 'is_room': False
    })

@login_required
@permission_required('p_approve_bill')
def transaction_approve(request, pk):
    from .forms import ApprovalPricingFormSet, TransactionHeaderForm
    transaction = get_object_or_404(Transaction, pk=pk)
    # _sync_transaction_category_defaults(transaction) # DISABLED: This was overwriting user inputs with category defaults
    
    # Immutability Check: Approved bills can only be viewed, not edited
    # Owners and Superusers can bypass this lock to make corrections
    is_owner = request.user.role == 'OWNER' or request.user.is_superuser
    is_locked = transaction.status in ['APPROVED', 'SUBMITTED', 'RELEASED'] and not is_owner
    is_room = _is_room_cat(transaction.transaction_category)
    
    if request.method == 'POST' and not is_locked:
        formset = ApprovalPricingFormSet(request.POST, instance=transaction)
        header_form = TransactionHeaderForm(request.POST, instance=transaction)
        if formset.is_valid() and header_form.is_valid():
            header_form.save()
            # Track changes for audit log
            changes = []
            for form in formset.forms:
                if form.has_changed():
                    if form.cleaned_data.get('DELETE'):
                        desc = form.instance.description or 'Item'
                        changes.append(f"Deleted item '{desc}'")
                    elif form.instance.pk is None:
                        desc = form.cleaned_data.get('description') or 'New Item'
                        changes.append(f"Added item '{desc}'")
                    else:
                        desc = form.instance.description or 'Item'
                        for field in form.changed_data:
                            old = form.initial.get(field)
                            new = form.cleaned_data.get(field)
                            changes.append(f"{desc}: {field} change {old} -> {new}")
            
            items = formset.save(commit=False)
            # Normalize each item by its own category's rules (isolated logic).
            normalize_items(transaction, items)
            for item in items:
                item.save()
            formset.save_m2m()
            for obj in formset.deleted_objects:
                obj.delete()
            _process_auto_product_saving(transaction, items, user=request.user)
            
            # Now set to APPROVED if it wasn't already approved or further
            if transaction.status not in ['APPROVED', 'SUBMITTED', 'RELEASED']:
                transaction.status = 'APPROVED'
            transaction.save()
            
            # Update Product Upscale & Base Values based on approved pricing
            for item in transaction.items.all():
                product = item.get_master_product()
                if product:
                    # Sync upscale from the price BEFORE global margin
                    product.upscale_value = item.unit_price_uplifted if item.unit_price_uplifted is not None else item.base_price
                    # Also update master cost to reflect latest supplier price
                    product.base_price = item.base_price 
                    product.save(update_fields=['upscale_value', 'base_price'])
            
            # Audit and complete approval
            audit_details = f"Approved Bill #{transaction.invoice_number}"
            if changes:
                audit_details += " | Pricing Updates: " + " | ".join(changes)
                
            log_audit(request.user, "Approved Transaction", audit_details, transaction=transaction)
            messages.success(request, 'Transaction approved and product upscale values updated.')
            return redirect('transaction_list')
        else:
            # Re-render with errors
            messages.error(request, "Failed to save prices. Please check the values below.")
    else:
        if not is_locked:
            # Pre-fill the owner uplift ONLY when it hasn't been set yet.
            # A GET must never overwrite an existing uplift — merely opening
            # this page used to silently reset owner pricing on lunch/room bills.
            for item in transaction.items.all():
                if item.unit_price_uplifted is None:
                    cat = transaction.transaction_category
                    is_same_price = cat and (cat.is_lunch or cat.is_room_reservation)
                    if is_same_price:
                        item.unit_price_uplifted = item.base_price
                    else:
                        master = item.get_master_product()
                        if master and item.base_price == master.base_price:
                            item.unit_price_uplifted = master.upscale_value
                        else:
                            item.unit_price_uplifted = item.base_price
                    item.save(update_fields=['unit_price_uplifted'])
        
        formset = ApprovalPricingFormSet(instance=transaction)
        header_form = TransactionHeaderForm(instance=transaction)
        
    is_lunch = _is_lunch_cat(transaction.transaction_category)
    return render(request, 'documents/transaction_approve.html', {
        'transaction': transaction,
        'formset': formset,
        'header_form': header_form,
        'is_locked': is_locked,
        'is_room': is_room,
        'is_lunch': is_lunch
    })

@login_required
@permission_required('p_view_audit_logs')
def audit_log_view(request):
    from documents.models import Transaction
    # Fetch transactions that have at least one audit log, newest first
    transactions_with_logs = Transaction.objects.filter(
        audit_logs__isnull=False
    ).distinct().order_by('-created_at')
    # Annotate each transaction obj with its pre-fetched logs
    grouped = []
    for tx in transactions_with_logs:
        tx_logs = tx.audit_logs.all().order_by('-timestamp')
        grouped.append({'transaction': tx, 'logs': tx_logs})
    # Orphan logs not attached to any transaction
    orphan_logs = AuditLog.objects.filter(transaction__isnull=True).order_by('-timestamp')
    return render(request, 'audit/log_list.html', {'grouped': grouped, 'orphan_logs': orphan_logs})

@login_required
def print_invoice(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk)
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills or (request.user.p_print_documents and transaction.creator == request.user)):
        messages.error(request, "Access Denied: You do not have permission to print this document.")
        return redirect('home')
        
    log_audit(request.user, "Printed Document", f"Printed Invoice for Bill #{transaction.invoice_number} (ID: {transaction.id})", transaction=transaction)
    return render(request, 'documents/print_invoice.html', {'transaction': transaction})

@login_required
def print_quotation(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk)
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills or (request.user.p_print_documents and transaction.creator == request.user)):
        messages.error(request, "Access Denied: You do not have permission to print this document.")
        return redirect('home')

    log_audit(request.user, "Printed Document", f"Printed Quotation for Bill #{transaction.invoice_number} (ID: {transaction.id})", transaction=transaction)
    return render(request, 'documents/print_quotation.html', {'transaction': transaction})

@login_required
def print_challan(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk)
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills or (request.user.p_print_documents and transaction.creator == request.user)):
        messages.error(request, "Access Denied: You do not have permission to print this document.")
        return redirect('home')

    log_audit(request.user, "Printed Document", f"Printed Challan for Bill #{transaction.invoice_number} (ID: {transaction.id})", transaction=transaction)
    return render(request, 'documents/print_challan.html', {'transaction': transaction})

@login_required
def print_single_challan(request, pk, item_pk):
    transaction = get_object_or_404(Transaction, pk=pk)
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills or (request.user.p_print_documents and transaction.creator == request.user)):
        messages.error(request, "Access Denied: You do not have permission to print this document.")
        return redirect('home')

    target_item = get_object_or_404(TransactionItem, pk=item_pk, transaction=transaction)
    log_audit(request.user, "Printed Document", f"Printed Single Challan for item {target_item.description} (TX ID: {transaction.id})", transaction=transaction)
    return render(request, 'documents/print_challan.html', {'transaction': transaction, 'single_item': target_item})

@login_required
def print_lunch_daily_challan(request, pk, date_str):
    """Print a combined challan for all lunch items on a specific date."""
    from django.utils.dateparse import parse_date
    transaction = get_object_or_404(Transaction, pk=pk)
    # Security Check (same policy as the other print views)
    if not (request.user.is_superuser or request.user.p_view_bills or (request.user.p_print_documents and transaction.creator == request.user)):
        messages.error(request, "Access Denied: You do not have permission to print this document.")
        return redirect('home')
    entry_date = parse_date(date_str)
    if not entry_date:
        from django.http import Http404
        raise Http404("Invalid date")
    date_items = transaction.items.filter(entry_date=entry_date).order_by('id')
    log_audit(
        request.user, "Printed Document",
        f"Printed Lunch Daily Challan for {date_str}",
        transaction=transaction
    )
    return render(request, 'documents/print_challan.html', {
        'transaction': transaction,
        'date_items': date_items,
        'challan_date': entry_date,
    })

@login_required
def print_mushok(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk)
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills or (request.user.p_print_documents and transaction.creator == request.user)):
        messages.error(request, "Access Denied: You do not have permission to print this document.")
        return redirect('home')

    log_audit(request.user, "Printed Document", f"Printed Mushok for Bill #{transaction.invoice_number} (ID: {transaction.id})", transaction=transaction)
    return render(request, 'documents/print_mushok.html', {'transaction': transaction})

@login_required
def print_multiple(request, doc_type):
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills):
        messages.error(request, "Access Denied: You do not have permission to print documents.")
        return redirect('home')

    ids = request.GET.get('ids', '')
    if not ids:
        messages.error(request, "No documents selected for printing.")
        return redirect('bill_summary')
        
    id_list = [int(i) for i in ids.split(',') if i.strip().isdigit()]
    transactions = Transaction.objects.filter(pk__in=id_list, status__in=['APPROVED', 'SUBMITTED', 'RELEASED'])
    
    if not transactions.exists():
        messages.error(request, "Selected documents are either not found or not approved.")
        return redirect('bill_summary')
        
    log_audit(request.user, "Printed Multiple Documents", f"Printed {doc_type} for {transactions.count()} bills")
    
    # Render the combined template
    return render(request, 'documents/print_multiple.html', {
        'transactions': transactions,
        'doc_type': doc_type
    })

@login_required
def print_bundle(request):
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills):
        messages.error(request, "Access Denied: You do not have permission to print documents.")
        return redirect('home')

    ids = request.GET.get('ids', '')
    types = request.GET.get('types', '')
    group_by = request.GET.get('group_by', 'transaction')
    
    if not ids:
        messages.error(request, "No documents selected for printing.")
        return redirect('bill_summary')
        
    id_list = [int(i) for i in ids.split(',') if i.strip().isdigit()]
    transactions = Transaction.objects.filter(pk__in=id_list, status__in=['APPROVED', 'SUBMITTED', 'RELEASED'])
    
    if not transactions.exists():
        messages.error(request, "Selected documents are either not found or not approved.")
        return redirect('bill_summary')
        
    type_list = [t.strip().lower() for t in types.split(',') if t.strip()]
    if not type_list:
        messages.error(request, "No document types selected for printing.")
        return redirect('bill_summary')

    log_audit(request.user, "Printed Document Bundle", f"Printed bundle ({', '.join(type_list)}) grouped by {group_by} for {transactions.count()} bills")
    
    import json
    doc_types_json = json.dumps(type_list)
    
    return render(request, 'documents/print_bundle.html', {
        'transactions': transactions,
        'doc_types_json': doc_types_json,
        'doc_types': type_list,
        'group_by': group_by,
    })

@login_required
def print_received_summary(request):
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills or request.user.p_print_documents):
        messages.error(request, "Access Denied: You do not have permission to print documents.")
        return redirect('home')

    ids = request.GET.get('ids', '')
    if not ids:
        messages.error(request, "No documents selected.")
        return redirect('bill_summary')
        
    id_list = [int(i) for i in ids.split(',') if i.strip().isdigit()]
    transactions = Transaction.objects.filter(pk__in=id_list, status__in=['APPROVED', 'SUBMITTED', 'RELEASED']).order_by('created_at')
    
    if not transactions.exists():
        messages.error(request, "No valid approved transactions found.")
        return redirect('bill_summary')
        
    log_audit(request.user, "Printed Document", f"Printed Received Copy Summary for {transactions.count()} bills", transaction=transactions.first())
    
    total_amount = sum(t.grand_total for t in transactions)
    
    return render(request, 'documents/print_received_summary.html', {
        'transactions': transactions,
        'total_amount': total_amount,
    })

@login_required
@permission_required('p_view_bill_summary')
def bill_summary(request):
    date_to_str = request.GET.get('date_to')
    date_from_str = request.GET.get('date_from')
    date_to = parse_date(date_to_str) if date_to_str else date.today()
    date_from = parse_date(date_from_str) if date_from_str else None
    
    status_filter = request.GET.get('status', 'ALL')
    sort_by = request.GET.get('sort', '-created_at')

    # Base query for transactions up to the given date
    tx_query = Transaction.objects.filter(created_at__date__lte=date_to)
    if date_from:
        tx_query = tx_query.filter(created_at__date__gte=date_from)
    
    if status_filter == 'ALL':
        # Include all statuses
        pass
    else:
        tx_query = tx_query.filter(status=status_filter)


    # Sorting
    if sort_by in ['status', '-status', 'invoice_number', '-invoice_number', 'created_at', '-created_at']:
        tx_query = tx_query.order_by(sort_by)

    # Main KPI Totals
    tx_list = list(tx_query.select_related('transaction_category').prefetch_related('items'))
    
    total_bills = len(tx_list)
    grand_revenue = sum((tx.grand_total for tx in tx_list), 0)
    total_upscale_revenue = sum((tx.display_subtotal for tx in tx_list), 0)
    total_vat = sum((tx.total_vat for tx in tx_list), 0)
    
    # Pre-calculate overall original price to compute Gross Profit
    total_original_price = sum(sum((item.base_price * item.quantity for item in tx.items.all()), 0) for tx in tx_list)
    total_gross_profit = total_upscale_revenue - total_original_price
    
    # Additional insight metrics
    avg_bill_value = grand_revenue / total_bills if total_bills > 0 else 0
    profit_margin_pct = (total_gross_profit / total_upscale_revenue * 100) if total_upscale_revenue > 0 else 0
    
    # Status distribution
    status_counts = {'DRAFT': 0, 'PRICED': 0, 'APPROVED': 0, 'SUBMITTED': 0, 'RELEASED': 0}
    status_amounts = {'DRAFT': 0, 'PRICED': 0, 'APPROVED': 0, 'SUBMITTED': 0, 'RELEASED': 0}
    for tx in tx_list:
        if tx.status in status_counts:
            status_counts[tx.status] += 1
            gt = getattr(tx, 'grand_total', None) or 0
            try:
                status_amounts[tx.status] += float(gt)
            except (TypeError, ValueError):
                pass
    # Percentage of each status (for progress bars, 0-100)
    draft_pct = (status_counts['DRAFT'] / total_bills * 100) if total_bills > 0 else 0
    priced_pct = (status_counts['PRICED'] / total_bills * 100) if total_bills > 0 else 0
    approved_pct = (status_counts['APPROVED'] / total_bills * 100) if total_bills > 0 else 0
    submitted_pct = (status_counts['SUBMITTED'] / total_bills * 100) if total_bills > 0 else 0
    released_pct = (status_counts['RELEASED'] / total_bills * 100) if total_bills > 0 else 0


    # Breakdown by Category
    categories_data = {}
    for tx in tx_list:
        cat_name = tx.transaction_category.name if tx.transaction_category else "General"
        cat_color = getattr(tx.transaction_category, 'color_theme', 'gray') if tx.transaction_category else "gray"
        if cat_name not in categories_data:
            categories_data[cat_name] = {
                'name': cat_name,
                'color': cat_color,
                'count': 0,
                'original_price': 0,
                'subtotal': 0,
                'service_charge': 0,
                'duty': 0,
                'vat': 0,
                'tax': 0,
                'grand_total': 0,
                'transactions': []
            }
        
        # Calculate original supplier base price
        original_price = sum(item.base_price * item.quantity for item in tx.items.all())
        
        c = categories_data[cat_name]
        c['count'] += 1
        c['original_price'] += original_price
        c['subtotal'] += tx.display_subtotal
        c['service_charge'] += tx.total_service_charge
        c['duty'] += tx.total_duty
        c['vat'] += tx.total_vat
        c['tax'] += tx.total_tax
        c['grand_total'] += tx.grand_total
        
        # Add tx details for the dropdown
        c['transactions'].append({
            'invoice_number': tx.invoice_number or f'TX-{tx.pk}',
            'date': tx.created_at,
            'buyer': tx.buyer_name,
            'original_price': original_price,
            'upscale_price': tx.display_subtotal,
            'grand_total': tx.grand_total,
            'status': tx.status,
            'pk': tx.pk
        })

    # Removing Top Buyers tracking feature per user request.

    # Monthly Trend (for charts)
    trend_data = {}
    for tx in tx_list:
        month_label = tx.created_at.strftime("%Y-%m")
        if month_label not in trend_data:
            trend_data[month_label] = 0
        trend_data[month_label] += tx.grand_total
    
    sorted_months = sorted(trend_data.keys())
    chart_labels = []
    chart_values = []
    max_trend_value = 0
    for m in sorted_months[-12:]:
        chart_labels.append(m)
        val = float(trend_data[m])
        chart_values.append(val)
        if val > max_trend_value:
            max_trend_value = val

    trend_bars = []
    for i in range(len(chart_labels)):
        pct = (chart_values[i] / max_trend_value * 100) if max_trend_value > 0 else 0
        trend_bars.append({
            'label': chart_labels[i],
            'value': chart_values[i],
            'height_pct': max(pct, 2)
        })

    # Detailed Transactions List
    from django.utils import timezone
    now = timezone.now()
    seven_days_ago = now - __import__('datetime').timedelta(days=7)
    detailed_transactions = []
    for tx in tx_list:
        original_price = sum((item.base_price * item.quantity for item in tx.items.all()), 0)
        profit = tx.display_subtotal - original_price
        is_newly_released = (
            tx.status == 'RELEASED' and
            tx.status_updated_at is not None and
            tx.status_updated_at >= seven_days_ago
        )
        desc = "-"
        first_item = tx.items.first()
        if first_item:
            desc = first_item.description
            if len(desc) > 40:
                desc = desc[:37] + "..."
        
        detailed_transactions.append({
            'tx_obj': tx,
            'date': tx.created_at,
            'invoice_number': tx.invoice_number,
            'description': desc,
            'original_price': original_price,
            'upscale_price': tx.display_subtotal,
            'vat': tx.total_vat,
            'tax': tx.total_tax,
            'grand_total': tx.grand_total,
            'profit': profit,
            'status': tx.status,
            'pk': tx.pk,
            'is_newly_released': is_newly_released,
        })

    context = {
        'date_from': date_from.strftime("%Y-%m-%d") if date_from else '',
        'date_to': date_to.strftime("%Y-%m-%d"),
        'total_bills': total_bills,
        'grand_revenue': grand_revenue,
        'total_vat': total_vat,
        'total_upscale_revenue': total_upscale_revenue,
        'total_gross_profit': total_gross_profit,
        'total_original_price': total_original_price,
        'avg_bill_value': avg_bill_value,
        'profit_margin_pct': profit_margin_pct,
        'status_counts': status_counts,
        'status_amounts': status_amounts,
        'draft_pct': draft_pct,
        'priced_pct': priced_pct,
        'approved_pct': approved_pct,
        'submitted_pct': submitted_pct,
        'released_pct': released_pct,

        'category_breakdown': list(categories_data.values()),
        'recent_bills': list(tx_query.order_by('-created_at')[:5]),
        'trend_bars': trend_bars,
        'current_status': status_filter,
        'current_sort': sort_by,
        'detailed_transactions': detailed_transactions,
    }
    return render(request, 'documents/bill_summary.html', context)

@login_required
def party_list(request):
    parties = BusinessParty.objects.all().order_by('name')
    return render(request, 'documents/party_list.html', {'parties': parties})

@login_required
@permission_required('p_manage_contacts')
def party_create(request):
    if request.method == 'POST':
        form = BusinessPartyForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, 'Contact saved successfully.')
            return redirect('party_list')
    else:
        form = BusinessPartyForm()
    return render(request, 'documents/party_form.html', {'form': form})

@login_required
def category_list(request):
    categories = TransactionCategory.objects.all().order_by('name')
    return render(request, 'documents/category_list.html', {'categories': categories})

@login_required
@permission_required('p_manage_categories')
def category_create(request):
    if request.method == 'POST':
        form = TransactionCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category saved successfully.')
            return redirect('category_list')
    else:
        form = TransactionCategoryForm()
    return render(request, 'documents/category_form.html', {'form': form})

@login_required
@owner_or_accountant_required
def transaction_update(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk)
    # _sync_transaction_category_defaults(transaction) # DISABLED: This was overwriting user inputs with category defaults
    
    # Immutability Check: Only Owners/Admins can edit after DRAFT
    is_admin = request.user.role == 'OWNER' or request.user.is_superuser
    if not is_admin and transaction.status != 'DRAFT':
        messages.error(request, "Access Denied: This bill is finalized or submitted for pricing and is now immutable.")
        return redirect('transaction_list')

    cat_name = transaction.transaction_category.name.lower() if transaction.transaction_category else ""
    is_lunch = _is_lunch_cat(transaction.transaction_category)
    is_room = _is_room_cat(transaction.transaction_category)
    # Category logic is isolated in category_rules.py.
    FormSetClass = rules_for_category(transaction.transaction_category).entry_formset()

    if request.method == 'POST':
        form = TransactionForm(request.POST, instance=transaction)
        formset = FormSetClass(request.POST, instance=transaction)
        if form.is_valid() and formset.is_valid():
            changes = []
            for field in form.changed_data:
                old_val = form.initial.get(field, 'Blank')
                new_val = form.cleaned_data.get(field, 'Blank')
                changes.append(f"Header field [{field}]: '{old_val}' changed to '{new_val}'")

            for item_form in formset.forms:
                if item_form.has_changed():
                    if is_lunch:
                        rest = item_form.cleaned_data.get('restaurant_name', 'Item')
                        edate = item_form.cleaned_data.get('entry_date', '')
                        changes.append(f"Modified daily entry for {rest} on {edate}")
                    elif is_room:
                        desc = item_form.cleaned_data.get('description', 'Item')
                        changes.append(f"Modified room row '{desc}'")
                    else:
                        desc = item_form.cleaned_data.get('description', 'Item')
                        changes.append(f"Modified row item details for '{desc}'")

            if formset.deleted_forms:
                changes.append(f"Permanently removed {len(formset.deleted_forms)} line item(s)")

            # Create a snapshot of the transaction before applying changes
            _create_transaction_snapshot(transaction, request.user)

            form.save()
            items = formset.save(commit=False)
            # Normalize each item by its own category's rules (isolated logic).
            normalize_items(transaction, items)
            for item in items:
                # If base_price changed or it's a new item, clear the uplifted price to force recalculation
                if item.pk:
                    try:
                        original = TransactionItem.objects.get(pk=item.pk)
                        if original.base_price != item.base_price:
                            item.unit_price_uplifted = None
                    except TransactionItem.DoesNotExist:
                        item.unit_price_uplifted = None
                else:
                    item.unit_price_uplifted = None
                
                item.save()
            for obj in formset.deleted_objects:
                obj.delete()

            # Auto-save products
            _process_auto_product_saving(transaction, items, user=request.user)

            details = f"Updated Bill #{transaction.invoice_number}."
            if changes:
                details += " Modifications: " + " | ".join(changes)

            log_audit(request.user, "Updated Transaction", details, transaction=transaction)
            messages.success(request, 'Transaction updated successfully.')
            return redirect('transaction_list')
        else:
            for field, errors in form.errors.items():
                messages.error(request, f"{field.replace('_', ' ').title()}: {', '.join(errors)}")
            for i, f in enumerate(formset.forms):
                for field, errors in f.errors.items():
                    messages.error(request, f"Row {i+1} ({field.replace('_', ' ').title()}): {', '.join(errors)}")
            if formset.non_form_errors():
                messages.error(request, f"Formset error: {', '.join(formset.non_form_errors())}")
    else:

        form = TransactionForm(instance=transaction)
        formset = FormSetClass(instance=transaction)
    parties = BusinessParty.objects.all()
    # Unified entry form: every category uses the one item table (normal mode).
    return render(request, 'documents/transaction_form.html', {
        'form': form, 'formset': formset, 'parties': parties,
        'is_update': True, 'is_lunch': False, 'is_room': False
    })

@login_required
@permission_required('p_delete_bill')
def transaction_delete(request, pk):
    obj = get_object_or_404(Transaction, pk=pk)
    
    # Immutability Check: Only Owners/Admins can delete after DRAFT
    is_admin = request.user.role == 'OWNER' or request.user.is_superuser
    if not is_admin and obj.status != 'DRAFT':
        messages.error(request, "Access Denied: Bills can only be deleted while in DRAFT status.")
        return redirect('transaction_list')

    if request.method == 'POST':
        num = obj.invoice_number
        obj.delete()
        log_audit(request.user, "Deleted Transaction", f"Deleted Bill #{num}")
        messages.success(request, 'Transaction deleted.')
        return redirect('transaction_list')
    return render(request, 'documents/confirm_delete.html', {'object_name': f"Transaction {obj.invoice_number}", 'cancel_url': 'transaction_list'})

@login_required
@permission_required('p_manage_contacts')
def party_update(request, pk):
    obj = get_object_or_404(BusinessParty, pk=pk)
    if request.method == 'POST':
        form = BusinessPartyForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Contact updated.')
            return redirect('party_list')
    else:
        form = BusinessPartyForm(instance=obj)
    return render(request, 'documents/party_form.html', {'form': form})

@login_required
@permission_required('p_manage_contacts')
def party_delete(request, pk):
    obj = get_object_or_404(BusinessParty, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Contact deleted.')
        return redirect('party_list')
    return render(request, 'documents/confirm_delete.html', {'object_name': f"Contact: {obj.name}", 'cancel_url': 'party_list'})

@login_required
@permission_required('p_manage_categories')
def category_update(request, pk):
    obj = get_object_or_404(TransactionCategory, pk=pk)
    if request.method == 'POST':
        form = TransactionCategoryForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category updated.')
            return redirect('category_list')
    else:
        form = TransactionCategoryForm(instance=obj)
    return render(request, 'documents/category_form.html', {'form': form})

@login_required
def category_defaults_api(request, pk):
    from django.http import JsonResponse
    from django.utils import timezone
    cat = get_object_or_404(TransactionCategory, pk=pk)
    
    # Calculate next serial for this category using robust Max+1 logic
    next_idx = _get_next_serial_number(cat)
    date_str = timezone.now().strftime("%Y%m")
    
    prefix = cat.invoice_prefix or "INV"
    next_inv = f"{prefix}-{date_str}-{next_idx:04d}"
    next_chl = f"CHL-{prefix}-{date_str}-{next_idx:04d}"
    
    return JsonResponse({
        'vat': str(cat.default_vat),
        'tax': str(cat.default_tax),
        'duty': str(cat.default_duty),
        'service_charge': str(cat.default_service_charge),
        'profit_margin': str(cat.default_profit_margin),
        'next_invoice': next_inv,
        'next_challan': next_chl,
        'prefix': prefix,
        'supplier': {
            'name': cat.default_supplier.name if cat.default_supplier else '',
            'bin': cat.default_supplier.bin_number if cat.default_supplier else '',
            'address': cat.default_supplier.address if cat.default_supplier else '',
            'contact': cat.default_supplier.contact if cat.default_supplier else '',
        },
        'buyer': {
            'name': cat.default_buyer.name if cat.default_buyer else '',
            'bin': cat.default_buyer.bin_number if cat.default_buyer else '',
            'address': cat.default_buyer.address if cat.default_buyer else '',
            'contact': cat.default_buyer.contact if cat.default_buyer else '',
        }
    })

@login_required
@permission_required('p_manage_categories')
def category_delete(request, pk):
    obj = get_object_or_404(TransactionCategory, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Category deleted.')
        return redirect('category_list')
    return render(request, 'documents/confirm_delete.html', {'object_name': f"Category: {obj.name}", 'cancel_url': 'category_list'})

@login_required
@permission_required('p_edit_bill')
def transaction_update_status(request, pk):
    if request.method == 'POST':
        tx = get_object_or_404(Transaction, pk=pk)
        new_status = request.POST.get('status')
        old_status = tx.status
        # Non-admins may only move a bill FORWARD through the workflow;
        # rolling back (e.g. RELEASED -> DRAFT) requires Owner/superuser.
        is_admin = request.user.role == 'OWNER' or request.user.is_superuser
        STATUS_ORDER = ['DRAFT', 'PRICED', 'APPROVED', 'SUBMITTED', 'RELEASED']
        if (not is_admin and new_status in STATUS_ORDER and old_status in STATUS_ORDER
                and STATUS_ORDER.index(new_status) < STATUS_ORDER.index(old_status)):
            messages.error(request, "Access Denied: Only the Owner can move a bill back to an earlier status.")
            return redirect(request.META.get('HTTP_REFERER', 'bill_summary'))
        if new_status in dict(Transaction.STATUS_CHOICES) and old_status != new_status:
            from django.utils import timezone
            tx.status = new_status
            tx.status_updated_at = timezone.now()
            tx.save(update_fields=['status', 'status_updated_at'])
            log_audit(request.user, "Status Update", f"Changed status of {tx.invoice_number} from {old_status} to {new_status}.", transaction=tx)
            messages.success(request, f"Status of {tx.invoice_number or 'Transaction'} updated to {new_status}.")
        return redirect(request.META.get('HTTP_REFERER', 'bill_summary'))
    return redirect('bill_summary')


@login_required
@permission_required('p_view_gate_entry')
def gate_entry(request):
    from datetime import timedelta, datetime
    from django.db.models import Q
    today = date.today()
    date_from_str = request.GET.get('date_from', '')
    date_to_str   = request.GET.get('date_to', today.strftime('%Y-%m-%d'))

    date_from = parse_date(date_from_str) if date_from_str else None
    date_to   = parse_date(date_to_str)   if date_to_str   else today

    meal_keywords = ['tiffin', 'dinner', 'iftar', 'seheri']
    meal_q = Q()
    for kw in meal_keywords:
        meal_q |= Q(transaction_category__name__icontains=kw)

    # 1. Normal Transactions: Filter by `created_at__date` directly in DB
    normal_qs = Transaction.objects.prefetch_related('items').select_related('transaction_category').exclude(status='DRAFT').exclude(meal_q).order_by('created_at')
    if date_from:
        normal_qs = normal_qs.filter(created_at__date__gte=date_from)
    if date_to:
        normal_qs = normal_qs.filter(created_at__date__lte=date_to)

    # 2. Meal Transactions: Fetch all, filter items by parse in memory
    meal_qs = Transaction.objects.prefetch_related('items').select_related('transaction_category').filter(meal_q)


    invoices = []
    grand_qty   = 0
    grand_items = 0

    # Process Normal
    for tx in normal_qs:
        items = list(tx.items.all())
        item_rows = []
        for it in items:
            item_rows.append({
                'description': it.description,
                'unit':        it.unit,
                'quantity':    it.quantity,
                'base_price':  it.base_price,
                'total':       it.base_price * it.quantity,
            })
            grand_qty   += it.quantity
            grand_items += 1
        
        invoices.append({
            'invoice_number': tx.invoice_number or f'TX-{tx.pk}',
            'date':           tx.created_at,
            'status':         tx.status,
            'buyer':          tx.buyer_name,
            'items':          item_rows,
            'item_count':     len(item_rows),
        })

    # Process Meals & Lunch (Itemwise entry)
    for tx in meal_qs:
        tx_date = tx.created_at.date() if tx.created_at else today
        cat_name = tx.transaction_category.name if tx.transaction_category else "Meal"
        is_lunch_tx = any(kw in cat_name.lower() for kw in ['lunch', 'tiffin', 'dinner', 'iftar'])

        items = list(tx.items.all())
        items_by_date = {}
        for it in items:
            # For Lunch & other meals: use entry_date field directly if present
            if it.entry_date:
                item_date = it.entry_date
            else:
                item_date = None
                d_str = it.description.strip()
                try:
                    if '/' in d_str:
                        item_date = datetime.strptime(d_str, "%d/%m/%Y").date()
                    elif '-' in d_str:
                        item_date = datetime.strptime(d_str, "%Y-%m-%d").date()
                    else:
                        item_date = tx_date
                except ValueError:
                    item_date = tx_date

            if item_date not in items_by_date:
                items_by_date[item_date] = []
            items_by_date[item_date].append(it)

        for item_date, daily_items in items_by_date.items():
            if date_from and item_date < date_from:
                continue
            if date_to and item_date > date_to:
                continue

            item_rows = []
            for it in daily_items:
                if it.entry_date:
                    label = f"{it.restaurant_name or cat_name} ({cat_name.title()})"
                else:
                    label = f"{cat_name} - {it.description}"
                item_rows.append({
                    'description':     label,
                    'restaurant_name': it.restaurant_name,
                    'unit':            it.unit,
                    'quantity':        it.quantity,
                    'base_price':      it.base_price,
                    'total':           it.base_price * it.quantity,
                    'item_pk':         it.pk,
                    'tx_pk':           tx.pk,
                })
                grand_qty += it.quantity
                grand_items += 1

            if item_rows:
                invoices.append({
                    'invoice_number': tx.invoice_number or f'TX-{tx.pk}',
                    'date':           item_date,
                    'status':         tx.status,
                    'buyer':          tx.buyer_name,
                    'items':          item_rows,
                    'item_count':     len(item_rows),
                    'is_lunch':       is_lunch_tx,
                    'tx_pk':          tx.pk,
                })
                
    # Sort the resulting list by date
    invoices.sort(key=lambda x: x['date'].date() if hasattr(x['date'], 'date') else x['date'])

    context = {
        'invoices':   invoices,
        'date_from':  date_from.strftime('%Y-%m-%d') if date_from else '',
        'date_to':    date_to.strftime('%Y-%m-%d')   if date_to   else '',
        'grand_qty':  grand_qty,
        'grand_items': grand_items,
        'invoice_count': len(invoices),
    }
    return render(request, 'documents/gate_entry.html', context)

# ── Product Management ─────────────────────────────────────────

@login_required
def product_list(request):
    is_owner = request.user.role == 'OWNER' or request.user.is_superuser
    
    # Handle Sorting
    sort_query = request.GET.get('sort', 'category') # Default sort by category
    sort_mapping = {
        'name': 'name',
        '-name': '-name',
        'category': 'category__name',
        '-category': '-category__name',
        'status': 'is_approved',
        '-status': '-is_approved',
    }
    sort_field = sort_mapping.get(sort_query, 'category__name')

    if is_owner:
        products = Product.objects.all().select_related('category', 'added_by').order_by(sort_field, 'name')
    else:
        from django.db.models import Q
        # Non-owners see approved products OR products they added themselves
        products = Product.objects.filter(
            Q(is_approved=True) | Q(added_by=request.user)
        ).select_related('category', 'added_by').order_by(sort_field, 'name')
        
    return render(request, 'documents/product_list.html', {
        'products': products,
        'is_owner': is_owner,
        'current_sort': sort_query
    })

@login_required
def product_create(request):
    if not (request.user.role == 'SUPPLIER' or request.user.p_manage_inventory or request.user.is_superuser):
        messages.error(request, "Access Denied: You do not have permission to add products.")
        return redirect('product_list')
        
    if request.method == 'POST':
        form = ProductForm(request.POST, user=request.user)
        if form.is_valid():
            product = form.save(commit=False)
            product.added_by = request.user
            # If owner creates it, it's auto-approved
            if request.user.role == 'OWNER' or request.user.is_superuser:
                product.is_approved = True
            else:
                product.is_approved = False
            product.save()
            messages.success(request, 'Product added successfully. Pending Owner approval.' if not product.is_approved else 'Product added and approved.')
            return redirect('product_list')
    else:
        form = ProductForm(user=request.user)
    return render(request, 'documents/product_form.html', {'form': form, 'title': 'Add New Product'})

@login_required
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    
    # Check if user can edit
    can_edit = (
        request.user.role == 'OWNER' or 
        request.user.is_superuser or 
        (request.user.role == 'SUPPLIER' and product.added_by == request.user) or
        (request.user.p_manage_inventory and product.added_by == request.user)
    )
    
    if not can_edit:
        messages.error(request, "You can only edit products you added or have management permissions for.")
        return redirect('product_list')

    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Product updated.')
            return redirect('product_list')
    else:
        form = ProductForm(instance=product, user=request.user)
    return render(request, 'documents/product_form.html', {'form': form, 'title': 'Edit Product'})

@login_required
@owner_required
def product_approve(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if not product.is_approved:
        product.is_approved = True
        product.save(update_fields=['is_approved'])
        messages.success(request, f"Product '{product.name}' approved.")
        log_audit(request.user, "Approved Product", f"Approved {product.name} (ID: {product.id})")
    return redirect('product_list')

@login_required
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    
    # Only Owners or the original adder can delete (if unapproved)
    can_delete = (
        request.user.role == 'OWNER' or 
        request.user.is_superuser or 
        (product.added_by == request.user and not product.is_approved)
    )
    
    if not can_delete:
        messages.error(request, "You cannot delete this product.")
        return redirect('product_list')
        
    if request.method == 'POST':
        product.delete()
        messages.success(request, 'Product deleted.')
        return redirect('product_list')
    return render(request, 'documents/confirm_delete.html', {
        'object_name': f"Product: {product.name}",
        'cancel_url': 'product_list'
    })

@login_required
def product_search_api(request):
    from django.http import JsonResponse
    category_id = request.GET.get('category_id')
    query = request.GET.get('q', '').lower()
    
    products = Product.objects.all()
    if category_id:
        products = products.filter(category_id=category_id)
    
    if query:
        products = products.filter(name__icontains=query)
    
    data = []
    for p in products[:20]:
        data.append({
            'id': p.id,
            'name': p.name,
            'base_price': str(p.upscale_value), # Return Upscale Value as the default base price
            'unit': p.unit,
        })
    return JsonResponse({'results': data})

# ── Requisitions ──────────────────────────────────────────────
@login_required
@permission_required('p_manage_requisitions')
def requisition_list(request):
    # Buyers see only their own requisitions
    requisitions = Transaction.objects.filter(creator=request.user).order_by('-created_at')
    return render(request, 'documents/requisition_list.html', {'requisitions': requisitions})

@login_required
@permission_required('p_manage_requisitions')
def requisition_create(request):
    if request.method == 'POST':
        form = RequisitionForm(request.POST)
        formset = RequisitionItemFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            requisition = form.save(commit=False)
            requisition.status = 'DRAFT'
            requisition.creator = request.user
            
            # Auto-apply category financial defaults & Parties
            if requisition.transaction_category:
                cat = requisition.transaction_category
                requisition.vat_percentage = cat.default_vat
                requisition.tax_percentage = cat.default_tax
                requisition.duty_percentage = cat.default_duty
                requisition.service_charge_percentage = cat.default_service_charge
                requisition.profit_margin = cat.default_profit_margin
                
                # Copy party info if not already set
                if cat.default_supplier and not requisition.supplier_name:
                    requisition.supplier_name = cat.default_supplier.name
                    requisition.supplier_bin = cat.default_supplier.bin_number
                    requisition.supplier_address = cat.default_supplier.address
                    requisition.supplier_contact = cat.default_supplier.contact
                
                if cat.default_buyer and not requisition.buyer_name:
                    requisition.buyer_name = cat.default_buyer.name
                    requisition.buyer_bin = cat.default_buyer.bin_number
                    requisition.buyer_address = cat.default_buyer.address
                    requisition.buyer_contact = cat.default_buyer.contact

            requisition.save()
            
            items = formset.save(commit=False)
            for item in items:
                item.transaction = requisition
                item.save()
                
            # Process auto product saving if needed (optional for requisitions)
            _process_auto_product_saving(requisition, items, user=request.user)
            
            messages.success(request, 'Requisition submitted successfully and saved as a Draft Quotation.')
            return redirect('requisition_list')
        else:
            for field, errors in form.errors.items():
                messages.error(request, f"{field.replace('_', ' ').title()}: {', '.join(errors)}")
            for i, f in enumerate(formset.forms):
                for field, errors in f.errors.items():
                    messages.error(request, f"Row {i+1} ({field.replace('_', ' ').title()}): {', '.join(errors)}")
            if formset.non_form_errors():
                messages.error(request, f"Formset error: {', '.join(formset.non_form_errors())}")
    else:

        # Initial buyer name handled in RequisitionForm.__init__
        form = RequisitionForm()
        formset = RequisitionItemFormSet()
        
    return render(request, 'documents/requisition_form.html', {
        'form': form,
        'formset': formset,
        'is_update': False
    })

@login_required
@permission_required('p_manage_requisitions')
def requisition_update(request, pk):
    # Allow Owners and Accountants to edit any requisition, but others only their own
    if request.user.role in ['OWNER', 'ACCOUNTANT'] or request.user.is_superuser:
        requisition = get_object_or_404(Transaction, pk=pk)
    else:
        requisition = get_object_or_404(Transaction, pk=pk, creator=request.user)
    
    if requisition.status != 'DRAFT':
        messages.error(request, "You can only edit requisitions that are still in Draft status.")
        return redirect('requisition_list')
        
    if request.method == 'POST':
        form = RequisitionForm(request.POST, instance=requisition)
        formset = RequisitionItemFormSet(request.POST, instance=requisition)
        if form.is_valid() and formset.is_valid():
            requisition = form.save(commit=False)
            
            # Sync category financial defaults on update if needed
            if requisition.transaction_category:
                cat = requisition.transaction_category
                requisition.vat_percentage = cat.default_vat
                requisition.tax_percentage = cat.default_tax
                requisition.duty_percentage = cat.default_duty
                requisition.service_charge_percentage = cat.default_service_charge
                requisition.profit_margin = cat.default_profit_margin
                
            requisition.save()
            items = formset.save()
            _process_auto_product_saving(requisition, items, user=request.user)
            messages.success(request, 'Requisition updated successfully.')
            return redirect('requisition_list')
        else:
            for field, errors in form.errors.items():
                messages.error(request, f"{field.replace('_', ' ').title()}: {', '.join(errors)}")
            for i, f in enumerate(formset.forms):
                for field, errors in f.errors.items():
                    messages.error(request, f"Row {i+1} ({field.replace('_', ' ').title()}): {', '.join(errors)}")
            if formset.non_form_errors():
                messages.error(request, f"Formset error: {', '.join(formset.non_form_errors())}")
    else:

        form = RequisitionForm(instance=requisition)
        formset = RequisitionItemFormSet(instance=requisition)
        
    return render(request, 'documents/requisition_form.html', {
        'form': form,
        'formset': formset,
        'is_update': True
    })

@login_required
@permission_required('p_manage_requisitions')
def requisition_delete(request, pk):
    # Allow Owners and Accountants to delete any requisition, but others only their own
    if request.user.role in ['OWNER', 'ACCOUNTANT'] or request.user.is_superuser:
        requisition = get_object_or_404(Transaction, pk=pk)
    else:
        requisition = get_object_or_404(Transaction, pk=pk, creator=request.user)
    
    if requisition.status != 'DRAFT':
        messages.error(request, "You can only delete requisitions that are still in Draft status.")
        return redirect('requisition_list')
        
    if request.method == 'POST':
        requisition.delete()
        messages.success(request, 'Requisition deleted.')
        return redirect('requisition_list')
        
    return render(request, 'documents/confirm_delete.html', {
        'object_name': f"Requisition from {requisition.created_at.strftime('%d %b')}",
        'cancel_url': 'requisition_list'
    })

# ── Visual/Image Requisitions ──────────────────────────────────
@login_required
def visual_requisition_list(request):
    """Gallery view showing shared requisition images."""
    if request.user.role == 'OWNER' or request.user.is_superuser:
        requisitions = VisualRequisition.objects.all()
    else:
        # Others only see what is shared with them
        requisitions = request.user.authorized_requisitions.all()
    
    return render(request, 'documents/visual_requisition_list.html', {
        'requisitions': requisitions.order_by('-created_at')
    })

@login_required
@permission_required('p_manage_visual_requisitions')
def visual_requisition_create(request):
    """Owner uploads a new image requisition."""
    if request.user.role != 'OWNER' and not request.user.is_superuser:
        messages.error(request, "Only the owner can upload image requisitions.")
        return redirect('visual_requisition_list')
        
    if request.method == 'POST':
        form = VisualRequisitionForm(request.POST, request.FILES)
        if form.is_valid():
            requisition = form.save(commit=False)
            requisition.uploader = request.user
            requisition.save()
            form.save_m2m() # Important for authorized_users
            
            log_audit(request.user, "Uploaded Requisition", f"Shared with {requisition.authorized_users.count()} users (ID: {requisition.id})")
            return redirect('visual_requisition_success', pk=requisition.pk)
    else:
        form = VisualRequisitionForm()
    
    return render(request, 'documents/visual_requisition_form.html', {'form': form})

@login_required
def visual_requisition_success(request, pk):
    """Success page with WhatsApp notification hub."""
    requisition = get_object_or_404(VisualRequisition, pk=pk)
    if requisition.uploader != request.user and not request.user.is_superuser:
        return redirect('visual_requisition_list')
        
    # Generate WhatsApp links for all authorized users
    import urllib.parse
    contacts = []
    
    current_domain = request.build_absolute_uri('/')[:-1]
    link = f"{current_domain}/visual-requisitions/"
    msg_template = f"*Visual Requisition Received*\nI have shared a new requisition image with you.\n🔗 View here: {link}\nPlease check and proceed."
    
    for user in requisition.authorized_users.all():
        if user.whatsapp_number:
            phone = "".join(filter(str.isdigit, str(user.whatsapp_number)))
            if phone.startswith('0') and len(phone) == 11: phone = '88' + phone
            elif len(phone) == 10: phone = '880' + phone
            
            wa_link = f"https://wa.me/{phone}?text={urllib.parse.quote(msg_template)}"
            contacts.append({
                'name': user.username.title(),
                'role': user.role,
                'whatsapp': wa_link
            })
            
    return render(request, 'documents/visual_requisition_success.html', {
        'requisition': requisition,
        'contacts': contacts
    })

@login_required
@permission_required('p_manage_visual_requisitions')
def visual_requisition_delete(request, pk):
    """Delete view for requisitions."""
    requisition = get_object_or_404(VisualRequisition, pk=pk)
    if requisition.uploader == request.user or request.user.is_superuser:
        requisition.delete()
        messages.success(request, "Requisition deleted.")
    return redirect('visual_requisition_list')

# ── Check Authorizations ────────────────────────────────────────

@login_required
@permission_required('p_manage_authorizations')
def authorization_list(request):
    """Dashboard for all check authorizations."""
    authorizations = CheckAuthorization.objects.all().order_by('-created_at')
    return render(request, 'documents/check_authorization_list.html', {
        'authorizations': authorizations
    })

@login_required
@permission_required('p_manage_authorizations')
def authorization_create(request):
    """Create a new check collection authorization."""
    from .forms import CheckAuthorizationForm
    from datetime import date
    import uuid
    
    if request.method == 'POST':
        form = CheckAuthorizationForm(request.POST)
        if form.is_valid():
            auth = form.save(commit=False)
            auth.authorized_by = request.user
            auth.save()
            messages.success(request, "Authorization created successfully.")
            return redirect('authorization_list')
    else:
        # Generate automatic reference number: AUTH-YEAR-RANDOM
        ref = f"AUTH-{date.today().year}-{str(uuid.uuid4())[:8].upper()}"
        form = CheckAuthorizationForm(initial={
            'reference_number': ref,
            'valid_from': date.today(),
            'trading_name': "NRS Traders"
        })
        
    return render(request, 'documents/check_authorization_form.html', {
        'form': form,
        'title': 'New Check Authorization'
    })

@login_required
@permission_required('p_manage_authorizations')
def authorization_update(request, pk):
    """Edit an existing check collection authorization."""
    from .forms import CheckAuthorizationForm
    auth = get_object_or_404(CheckAuthorization, pk=pk)
    
    if request.method == 'POST':
        form = CheckAuthorizationForm(request.POST, instance=auth)
        if form.is_valid():
            form.save()
            messages.success(request, "Authorization updated successfully.")
            return redirect('authorization_list')
    else:
        form = CheckAuthorizationForm(instance=auth)
        
    return render(request, 'documents/check_authorization_form.html', {
        'form': form,
        'title': 'Edit Check Authorization',
        'is_update': True
    })

@login_required
@permission_required('p_manage_authorizations')
def authorization_print(request, pk):
    """Render the printable authorization document."""
    auth = get_object_or_404(CheckAuthorization, pk=pk)
    return render(request, 'documents/check_authorization_print.html', {
        'auth': auth,
        'owner_name': "Md. Nahidur Rahman Sakib",
        'owner_designation': "Proprietor & CEO"
    })


@login_required
@permission_required('p_manage_authorizations')
def api_get_user_nid(request, user_id):
    """Fetch NID for a specific user for form auto-fill."""
    from accounts.models import User
    userobj = get_object_or_404(User, pk=user_id)
    return JsonResponse({
        'nid_number': userobj.nid_number or ""
    })

@login_required
def api_get_party_details(request, party_id):
    """Fetch Name and Address for a specific business party."""
    party = get_object_or_404(BusinessParty, pk=party_id)
    return JsonResponse({
        'name': party.name,
        'address': party.address
    })

@login_required
def print_layout_api(request, pk, doc_type):
    """Per-bill print layout store, scoped to (bill, document type).

    GET  -> {"store": <this bill's saved layout blob or null>}
    POST -> body is the whole editor store blob; upserts and returns {"ok": true}
    Each bill keeps its own layout, so editing one bill's print never affects
    another, and bulk printing applies each bill's own saved layout.
    """
    from .models import PrintLayout, Transaction
    valid = {c[0] for c in PrintLayout.DOC_TYPES}
    if doc_type not in valid:
        return JsonResponse({'error': 'invalid document type'}, status=400)
    transaction = get_object_or_404(Transaction, pk=pk)

    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body or '{}')
        except (ValueError, TypeError):
            return JsonResponse({'error': 'invalid JSON'}, status=400)
        if not isinstance(data, dict):
            return JsonResponse({'error': 'invalid payload'}, status=400)
        obj, _ = PrintLayout.objects.get_or_create(transaction=transaction, doc_type=doc_type)
        obj.data = data
        obj.updated_by = request.user if request.user.is_authenticated else None
        obj.save()
        return JsonResponse({'ok': True})

    obj = PrintLayout.objects.filter(transaction=transaction, doc_type=doc_type).first()
    return JsonResponse({'store': obj.data if obj else None})

import os
from django.contrib import messages
from django.shortcuts import redirect
from documents.sync import run_synchronization

@login_required
def sync_db_view(request):
    """View to trigger full synchronization with Supabase."""
    result = run_synchronization()
    if result["status"] == "success":
        messages.success(request, result["message"])
    else:
        messages.error(request, result["message"])
    return redirect(request.META.get('HTTP_REFERER', 'transaction_list'))

@login_required
def sync_status_api(request):
    """API endpoint to get sync status (count of unsynced items)."""
    from documents.models import (
        TransactionCategory, BusinessParty, Product, 
        Transaction, TransactionItem, VisualRequisition, CheckAuthorization
    )
    models = [TransactionCategory, BusinessParty, Product, Transaction, TransactionItem, VisualRequisition, CheckAuthorization]
    unsynced_count = sum(m.objects.filter(is_dirty=True).count() for m in models)
    has_supabase = bool(os.environ.get('SUPABASE_DATABASE_URL'))
    
    return JsonResponse({
        'unsynced_count': unsynced_count,
        'has_supabase': has_supabase
    })




# ── Quick Bill: type a whole bill instead of filling blanks ────────────────
def _quick_resolve_category(first_line):
    """Fuzzy-match a typed line (e.g. 'lunch') to a TransactionCategory."""
    import difflib
    if not first_line:
        return None
    first = str(first_line).strip().lower()
    cats = list(TransactionCategory.objects.all())
    for c in cats:
        n = c.name.strip().lower()
        if n == first or first in n or n in first:
            return c
    close = difflib.get_close_matches(first, [c.name.strip().lower() for c in cats], n=1, cutoff=0.6)
    if close:
        return next((c for c in cats if c.name.strip().lower() == close[0]), None)
    return None


def _quick_build_rows(category, raw_items):
    """Resolve raw items against the Product master.

    raw_items: [{'description', 'quantity', 'unit', 'unit_price'}]
    Returns preview rows with unit/price auto-filled and a per-row status, so
    the typed parser and the AI parser produce identical output.
    """
    import difflib
    scoped = list(Product.objects.filter(category=category)) if category else []
    everything = list(Product.objects.all())

    def match(name):
        n = (name or '').strip().lower()
        if not n:
            return None
        for pool in (scoped, everything):
            if not pool:
                continue
            for p in pool:                                    # exact (any length)
                if p.name.strip().lower() == n:
                    return p
            # Fuzzy/substring only for reasonably specific text, so a stray
            # word like "bad" doesn't inherit some product's price.
            if len(n) < 4:
                continue
            for p in pool:                                    # substring either way
                pn = p.name.strip().lower()
                if n in pn or pn in n:
                    return p
            close = difflib.get_close_matches(n, [p.name.strip().lower() for p in pool], n=1, cutoff=0.82)
            if close:
                hit = next((p for p in pool if p.name.strip().lower() == close[0]), None)
                if hit:
                    return hit
        return None

    rows = []
    for i, raw in enumerate(raw_items, start=1):
        desc = (raw.get('description') or '').strip()
        note, status = [], 'ok'

        qty = raw.get('quantity')
        try:
            qty = float(qty) if qty not in (None, '') else 1.0
            if qty <= 0:
                raise ValueError
        except (TypeError, ValueError):
            qty, status = 1.0, 'error'
            note.append('invalid quantity')

        price = raw.get('unit_price')
        try:
            price = float(price) if price not in (None, '') else None
            if price is not None and price < 0:
                raise ValueError
        except (TypeError, ValueError):
            price, status = None, 'error'
            note.append('invalid price')

        unit = (raw.get('unit') or '').strip() or None
        prod = match(desc) if desc else None
        if prod:
            if not unit:
                unit = prod.unit
            if price is None:
                saved = prod.upscale_value or prod.base_price or 0
                if saved:
                    price = float(saved)
                    note.append('price + unit from "%s"' % prod.name)
        if not desc:
            status = 'error'
            note.append('no description')
        if status == 'ok' and not price:
            status = 'needs_price'
            note.append('no saved price - enter it')

        rows.append({
            'idx': i,
            'description': desc,
            'unit': unit or 'Pcs',
            'quantity': qty,
            'unit_price': price,
            'matched_product': prod.name if prod else None,
            'status': status,
            'note': ', '.join(note),
        })
    return rows


@login_required
def quick_bill_parse(request):
    """Deterministic parser: 'name | qty | price' lines, optional category line."""
    import json, re
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        payload = json.loads(request.body or '{}')
    except (ValueError, TypeError):
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    text = (payload.get('text') or '').strip()
    if not text:
        return JsonResponse({'error': 'no text supplied'}, status=400)

    sep = re.compile(r'\s*[|\t;]\s*')
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    category, category_line = None, None
    if lines and len(sep.split(lines[0])) == 1:
        category = _quick_resolve_category(lines[0])
        if category is not None:
            category_line = lines[0]
            lines = lines[1:]
    if category is None and payload.get('category_id'):
        category = TransactionCategory.objects.filter(pk=payload.get('category_id')).first()

    raw_items = []
    for line in lines:
        parts = sep.split(line)
        raw_items.append({
            'description': parts[0] if parts else '',
            'quantity': parts[1] if len(parts) > 1 and parts[1].strip() != '' else None,
            'unit': None,
            'unit_price': parts[2] if len(parts) > 2 and parts[2].strip() != '' else None,
        })

    return JsonResponse({
        'ok': True,
        'category': ({'id': category.id, 'name': category.name} if category else None),
        'category_line': category_line,
        'rows': _quick_build_rows(category, raw_items),
    })


def _ai_extract_anthropic(api_key, model, system, text, schema):
    """Anthropic path: strict json_schema output, so the JSON is guaranteed."""
    import json, anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model or "claude-opus-4-8",
        max_tokens=4096,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": text}],
    )
    if getattr(resp, 'stop_reason', None) == 'refusal':
        raise RuntimeError('the model declined to process that text')
    raw = next(b.text for b in resp.content if b.type == 'text')
    return json.loads(raw)


def _ai_extract_openai_compatible(api_key, base_url, model, system, text):
    """NVIDIA NIM / any OpenAI-compatible endpoint, via stdlib urllib.

    These models don't reliably support strict schemas, so we ask for JSON and
    then defensively pull the first {...} block out of the reply.
    """
    import json, urllib.request, urllib.error
    url = (base_url or '').rstrip('/') + '/chat/completions'
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "temperature": 0.1,
        "max_tokens": 1500,
    }).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': 'Bearer %s' % api_key,
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        raise RuntimeError('provider returned HTTP %s: %s' % (e.code, detail))

    content = (data.get('choices') or [{}])[0].get('message', {}).get('content', '') or ''
    # Strip code fences / prose around the JSON object.
    start, end = content.find('{'), content.rfind('}')
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError('the model did not return JSON')
    return json.loads(content[start:end + 1])


@login_required
def quick_bill_ai_parse(request):
    """Free-form parser: hand messy text (a WhatsApp message, a note) to an LLM,
    get structured items back, then resolve them through the SAME product
    matching / preview pipeline as the typed parser.

    Provider is configurable: NVIDIA NIM (or any OpenAI-compatible endpoint) or
    Anthropic. Whichever is used, the user still confirms the preview.
    """
    import json
    from django.conf import settings

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    provider = (getattr(settings, 'AI_PROVIDER', '') or '').strip().lower()
    api_key = (getattr(settings, 'AI_API_KEY', '') or '').strip()
    anthropic_key = (getattr(settings, 'ANTHROPIC_API_KEY', '') or '').strip()
    if not provider and anthropic_key:
        provider, api_key = 'anthropic', anthropic_key
    if provider == 'anthropic' and not api_key:
        api_key = anthropic_key

    if not provider or not api_key:
        return JsonResponse({
            'error': 'AI parsing is not set up yet. Set AI_PROVIDER / AI_API_KEY in your .env '
                     'and restart, or use the plain "name | qty | price" format above.'
        }, status=503)

    try:
        payload = json.loads(request.body or '{}')
    except (ValueError, TypeError):
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    text = (payload.get('text') or '').strip()
    if not text:
        return JsonResponse({'error': 'no text supplied'}, status=400)

    known = list(TransactionCategory.objects.values_list('name', flat=True))
    schema = {
        "type": "object",
        "properties": {
            "category": {"type": ["string", "null"],
                         "description": "Best matching category name from the provided list, else null"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "The item / product name only"},
                        "quantity": {"type": "number"},
                        "unit": {"type": ["string", "null"]},
                        "unit_price": {"type": ["number", "null"],
                                       "description": "Per-unit price if stated, else null"},
                    },
                    "required": ["description", "quantity", "unit", "unit_price"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["category", "items"],
        "additionalProperties": False,
    }

    system = (
        "You extract billing line items from short, messy purchase messages for a "
        "Bangladeshi supply company. Amounts are in BDT. Return ONLY what the message "
        "actually states: never invent prices or quantities. If a quantity is missing, "
        "use 1. If a price is not stated, use null (it gets filled from a product "
        "database afterwards). Split combined lines into separate items.\n"
        "Available categories: " + ", ".join(known) + "\n"
        "Reply with ONLY a JSON object, no prose and no code fences, shaped exactly like:\n"
        '{"category": "<one of the categories above or null>", "items": '
        '[{"description": "item name", "quantity": 1, "unit": null, "unit_price": null}]}'
    )

    model = (getattr(settings, 'AI_MODEL', '') or '').strip()
    try:
        if provider == 'anthropic':
            data = _ai_extract_anthropic(api_key, model, system, text, schema)
        else:
            base_url = (getattr(settings, 'AI_BASE_URL', '') or '').strip()
            if not base_url:
                return JsonResponse({'error': 'AI_BASE_URL is not set for provider "%s".' % provider}, status=503)
            data = _ai_extract_openai_compatible(api_key, base_url, model, system, text)
    except Exception as exc:
        return JsonResponse({'error': 'AI request failed: %s' % exc}, status=502)

    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        return JsonResponse({'error': 'The AI reply was not in the expected shape. '
                                      'Try the plain "name | qty | price" format.'}, status=502)

    category = _quick_resolve_category(data.get('category')) if data.get('category') else None
    if category is None and payload.get('category_id'):
        category = TransactionCategory.objects.filter(pk=payload.get('category_id')).first()

    return JsonResponse({
        'ok': True,
        'ai': True,
        'category': ({'id': category.id, 'name': category.name} if category else None),
        'category_line': data.get('category'),
        'rows': _quick_build_rows(category, data.get('items') or []),
    })

@login_required
def api_inline_save_transaction(request, pk):
    """Zero-redirect in-place live edit endpoint for Bills Command Center."""
    import json
    from decimal import Decimal
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    transaction = get_object_or_404(Transaction, pk=pk)
    
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except Exception as e:
        return JsonResponse({'error': f'Invalid JSON payload: {str(e)}'}, status=400)
        
    # Update Header Fields
    if 'invoice_number' in data and data['invoice_number']:
        transaction.invoice_number = str(data['invoice_number']).strip()
    if 'challan_number' in data:
        transaction.challan_number = str(data['challan_number']).strip()
    if 'buyer_name' in data and data['buyer_name']:
        transaction.buyer_name = str(data['buyer_name']).strip()
    if 'supplier_name' in data:
        transaction.supplier_name = str(data['supplier_name']).strip()
    if 'status' in data and data['status'] in dict(Transaction.STATUS_CHOICES):
        transaction.status = data['status']
        
    transaction.save()
    
    # Update / Recreate Line Items
    items_data = data.get('items', [])
    if isinstance(items_data, list) and len(items_data) > 0:
        incoming_ids = [int(it.get('id')) for it in items_data if it.get('id') and str(it.get('id')).isdigit()]
        transaction.items.exclude(pk__in=incoming_ids).delete()
        
        for item_info in items_data:
            item_id = item_info.get('id')
            desc = str(item_info.get('description', '')).strip()
            try:
                qty = float(item_info.get('quantity', 1) or 1)
            except (ValueError, TypeError):
                qty = 1.0
                
            unit = str(item_info.get('unit', 'Pcs')).strip() or 'Pcs'
            
            try:
                base_price = Decimal(str(item_info.get('base_price', 0) or 0))
            except Exception:
                base_price = Decimal('0.00')
                
            try:
                uplifted_val = item_info.get('unit_price_uplifted')
                uplifted_price = Decimal(str(uplifted_val)) if uplifted_val is not None else base_price
            except Exception:
                uplifted_price = base_price
            
            if item_id and str(item_id).isdigit():
                it_obj = TransactionItem.objects.filter(pk=int(item_id), transaction=transaction).first()
                if it_obj:
                    it_obj.description = desc
                    it_obj.quantity = qty
                    it_obj.unit = unit
                    it_obj.base_price = base_price
                    it_obj.unit_price_uplifted = uplifted_price
                    it_obj.save()
            else:
                if desc:
                    TransactionItem.objects.create(
                        transaction=transaction,
                        description=desc,
                        quantity=qty,
                        unit=unit,
                        base_price=base_price,
                        unit_price_uplifted=uplifted_price
                    )

    transaction.refresh_from_db()
    _process_auto_product_saving(transaction, transaction.items.all(), user=request.user)
    log_audit(request.user, "Inline Saved Bill", f"Live edited Bill #{transaction.invoice_number} in Command Center.", transaction=transaction)

    # Build updated response payload
    updated_items = []
    for it in transaction.items.all():
        updated_items.append({
            'id': it.pk,
            'description': it.display_description,
            'quantity': float(it.quantity or 0),
            'unit': it.unit or 'Pcs',
            'base_price': f"{it.base_price:.2f}",
            'unit_price_uplifted': f"{(it.unit_price_uplifted if it.unit_price_uplifted is not None else it.base_price):.2f}",
            'billed_total': f"{it.billed_total:.2f}"
        })

    return JsonResponse({
        'ok': True,
        'pk': transaction.pk,
        'invoice_number': transaction.invoice_number or '-',
        'challan_number': transaction.challan_number or '-',
        'buyer_name': transaction.buyer_name,
        'supplier_name': transaction.supplier_name or '-',
        'status': transaction.status,
        'subtotal': f"{transaction.display_subtotal:.2f}",
        'vat': f"{transaction.total_vat:.2f}",
        'tax': f"{transaction.total_tax:.2f}",
        'grand_total': f"{transaction.grand_total:.2f}",
        'items': updated_items
    })
