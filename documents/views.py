from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required, user_passes_test
from accounts.decorators import (accountant_required, supplier_required, 
                                 owner_or_accountant_required, buyer_required, 
                                 permission_required, owner_required)
from .models import Transaction, BusinessParty, TransactionCategory, TransactionItem, Product, VisualRequisition, CheckAuthorization
from .forms import (TransactionForm, TransactionItemFormSet, SupplierPricingFormSet,
                    LunchItemFormSet, LunchSupplierPricingFormSet,
                    BusinessPartyForm, TransactionCategoryForm, ProductForm,
                    RequisitionForm, RequisitionItemFormSet, VisualRequisitionForm)
from django.contrib import messages
from audit.utils import log_audit
from audit.models import AuditLog
from django.utils.dateparse import parse_date
from datetime import date

def _is_lunch_cat(cat):
    if not cat:
        return False
    name = cat.name.lower()
    return any(keyword in name for keyword in ['lunch', 'tiffin', 'dinner', 'iftar'])

def _get_next_serial_number(category):
    from django.utils import timezone
    import re
    
    current_month_str = timezone.now().strftime("%Y%m")
    
    # Filter transactions for this category and current month
    # Our format is PREFIX-YYYYMM-XXXX
    txns = Transaction.objects.filter(
        transaction_category=category,
        invoice_number__contains=f"-{current_month_str}-"
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
    if not transaction.transaction_category or _is_lunch_cat(transaction.transaction_category):
        return
    
    for item in items:
        if not item.description: continue
        
        # Try to find matching product in this category
        product = Product.objects.filter(
            name=item.description,
            category=transaction.transaction_category
        ).first()
        
        if product:
            # If item has no price (like a new requisition), pull master values
            if item.base_price == 0:
                item.base_price = product.base_price
                if item.unit_price_uplifted is None:
                    item.unit_price_uplifted = product.upscale_value
                item.save(update_fields=['base_price', 'unit_price_uplifted'])
        else:
            # If it's a new item WITH a price (from a full bill), create it as a new Product
            if item.base_price > 0:
                Product.objects.create(
                    name=item.description,
                    category=transaction.transaction_category,
                    base_price=item.base_price,    # Initial actual cost
                    upscale_value=item.base_price, # Initial upscale value (owner can edit later)
                    unit=item.unit or 'Pcs',
                    added_by=user,
                    is_approved=(user and (user.role == 'OWNER' or user.is_superuser))
                )

@login_required
def home(request):
    return redirect('transaction_list')

@login_required
def transaction_list(request):
    transactions = Transaction.objects.all().order_by('-created_at')
    return render(request, 'documents/transaction_list.html', {'transactions': transactions})

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
        is_lunch = _is_lunch_cat(cat_obj)
        FormSetClass = LunchItemFormSet if is_lunch else TransactionItemFormSet
        formset = FormSetClass(request.POST)
        if form.is_valid() and formset.is_valid():
            transaction = form.save(commit=False)
            transaction.status = 'DRAFT'
            transaction.save()
            items = formset.save(commit=False)
            for item in items:
                item.transaction = transaction
                if is_lunch and not item.description:
                    item.description = str(item.entry_date or '') 
                item.save()
            for obj in formset.deleted_objects:
                obj.delete()
            
            # Auto-save products
            _process_auto_product_saving(transaction, items, user=request.user)

            audit_details = f"Created new DRAFT Bill #{transaction.invoice_number} under category {cat_obj.name if cat_obj else 'Uncategorized'}. Initialized with {len(items)} line item(s)."
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
    parties = BusinessParty.objects.all()
    return render(request, 'documents/transaction_form.html', {
        'form': form, 'formset': formset, 'parties': parties, 'is_lunch': is_lunch
    })

@login_required
@permission_required('p_price_bill')
def transaction_pricing(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk)
    
    # Immutability Check: Only Owners/Admins can edit after DRAFT
    is_admin = request.user.role == 'OWNER' or request.user.is_superuser
    if not is_admin and transaction.status != 'DRAFT':
        messages.error(request, "Access Denied: Pricing can only be managed while the bill is in DRAFT status.")
        return redirect('transaction_list')

    cat_name = transaction.transaction_category.name.lower() if transaction.transaction_category else ""
    is_lunch = _is_lunch_cat(transaction.transaction_category)
    is_meal = is_lunch

    if is_lunch:
        FormSetClass = LunchItemFormSet
    elif is_meal:
        FormSetClass = TransactionItemFormSet
    else:
        FormSetClass = SupplierPricingFormSet

    if request.method == 'POST':
        formset = FormSetClass(request.POST, instance=transaction)
        if formset.is_valid():
            changes = []
            if is_lunch:
                for item_form in formset.forms:
                    if item_form.has_changed():
                        rest = item_form.cleaned_data.get('restaurant_name', 'Item')
                        edate = item_form.cleaned_data.get('entry_date', '')
                        changes.append(f"Updated {rest} ({edate})")
                if formset.deleted_forms:
                    changes.append("Removed items")
                items = formset.save(commit=False)
                for item in items:
                    if not item.description:
                        item.description = str(item.entry_date or '')
                    item.save()
                for obj in formset.deleted_objects:
                    obj.delete()
            elif is_meal:
                for item_form in formset.forms:
                    if item_form.has_changed():
                        desc = item_form.cleaned_data.get('description', 'Item')
                        changes.append(f"Updated {desc}")
                if formset.deleted_forms:
                    changes.append("Removed items")
                formset.save()
            else:
                for item_form in formset.forms:
                    if item_form.has_changed() and 'base_price' in item_form.changed_data:
                        old_val = item_form.initial.get('base_price', '0')
                        new_val = item_form.cleaned_data.get('base_price', '0')
                        desc = item_form.instance.description or 'Item'
                        changes.append(f"{desc}: ৳{old_val} -> ৳{new_val}")
                formset.save()

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

    return render(request, 'documents/transaction_pricing.html', {
        'transaction': transaction, 'formset': formset, 'is_meal': is_meal, 'is_lunch': is_lunch
    })

@login_required
@permission_required('p_approve_bill')
def transaction_approve(request, pk):
    from .forms import ApprovalPricingFormSet
    transaction = get_object_or_404(Transaction, pk=pk)
    
    # Immutability Check: Approved bills can only be viewed, not edited
    is_locked = transaction.status == 'APPROVED'
    
    if request.method == 'POST' and not is_locked:
        formset = ApprovalPricingFormSet(request.POST, instance=transaction)
        if formset.is_valid():
            # Track changes for audit log
            changes = []
            for form in formset.forms:
                if form.has_changed():
                    desc = form.instance.description or 'Item'
                    for field in form.changed_data:
                        old = form.initial.get(field)
                        new = form.cleaned_data.get(field)
                        changes.append(f"{desc}: {field} change {old} -> {new}")
            
            formset.save()
            
            # Now set to APPROVED
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
            # ALWAYS sync with master inventory prices if a product match is found
            # This ensures the owner's set prices are the starting point in the input boxes
            for item in transaction.items.all():
                master = item.get_master_product()
                if master:
                    item.base_price = master.base_price
                    # Also pre-fill the owner uplift if it hasn't been set yet
                    if item.unit_price_uplifted is None:
                        item.unit_price_uplifted = master.upscale_value
                    item.save(update_fields=['base_price', 'unit_price_uplifted'])
        
        formset = ApprovalPricingFormSet(instance=transaction)
        
    return render(request, 'documents/transaction_approve.html', {
        'transaction': transaction,
        'formset': formset,
        'is_locked': is_locked
    })

@login_required
@owner_required
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
    transaction = get_object_or_404(Transaction, pk=pk, status='APPROVED')
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills or (request.user.p_print_documents and transaction.creator == request.user)):
        messages.error(request, "Access Denied: You do not have permission to print this document.")
        return redirect('home')
        
    log_audit(request.user, "Printed Document", f"Printed Invoice for Bill #{transaction.invoice_number} (ID: {transaction.id})", transaction=transaction)
    return render(request, 'documents/print_invoice.html', {'transaction': transaction})

@login_required
def print_quotation(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk, status='APPROVED')
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills or (request.user.p_print_documents and transaction.creator == request.user)):
        messages.error(request, "Access Denied: You do not have permission to print this document.")
        return redirect('home')

    log_audit(request.user, "Printed Document", f"Printed Quotation for Bill #{transaction.invoice_number} (ID: {transaction.id})", transaction=transaction)
    return render(request, 'documents/print_quotation.html', {'transaction': transaction})

@login_required
def print_challan(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk, status='APPROVED')
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
    transaction = get_object_or_404(Transaction, pk=pk, status='APPROVED')
    # Security Check
    if not (request.user.is_superuser or request.user.p_view_bills or (request.user.p_print_documents and transaction.creator == request.user)):
        messages.error(request, "Access Denied: You do not have permission to print this document.")
        return redirect('home')

    log_audit(request.user, "Printed Document", f"Printed Mushok for Bill #{transaction.invoice_number} (ID: {transaction.id})", transaction=transaction)
    return render(request, 'documents/print_mushok.html', {'transaction': transaction})

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
        detailed_transactions.append({
            'tx_obj': tx,
            'date': tx.created_at,
            'invoice_number': tx.invoice_number,
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
        form = BusinessPartyForm(request.POST)
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
    
    # Immutability Check: Only Owners/Admins can edit after DRAFT
    is_admin = request.user.role == 'OWNER' or request.user.is_superuser
    if not is_admin and transaction.status != 'DRAFT':
        messages.error(request, "Access Denied: This bill is finalized or submitted for pricing and is now immutable.")
        return redirect('transaction_list')

    cat_name = transaction.transaction_category.name.lower() if transaction.transaction_category else ""
    is_lunch = _is_lunch_cat(transaction.transaction_category)
    FormSetClass = LunchItemFormSet if is_lunch else TransactionItemFormSet

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
                    else:
                        desc = item_form.cleaned_data.get('description', 'Item')
                        changes.append(f"Modified row item details for '{desc}'")

            if formset.deleted_forms:
                changes.append(f"Permanently removed {len(formset.deleted_forms)} line item(s)")


            form.save()
            items = formset.save(commit=False)
            for item in items:
                if is_lunch and not item.description:
                    item.description = str(item.entry_date or '')
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
    return render(request, 'documents/transaction_form.html', {
        'form': form, 'formset': formset, 'parties': parties,
        'is_update': True, 'is_lunch': is_lunch
    })

@login_required
@owner_or_accountant_required
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
@owner_or_accountant_required
def party_update(request, pk):
    obj = get_object_or_404(BusinessParty, pk=pk)
    if request.method == 'POST':
        form = BusinessPartyForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Contact updated.')
            return redirect('party_list')
    else:
        form = BusinessPartyForm(instance=obj)
    return render(request, 'documents/party_form.html', {'form': form})

@login_required
@owner_or_accountant_required
def party_delete(request, pk):
    obj = get_object_or_404(BusinessParty, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Contact deleted.')
        return redirect('party_list')
    return render(request, 'documents/confirm_delete.html', {'object_name': f"Contact: {obj.name}", 'cancel_url': 'party_list'})

@login_required
@accountant_required
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
@accountant_required
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

    meal_keywords = ['tiffin', 'dinner', 'iftar', 'seheri', 'lunch']
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
def api_get_user_nid(request, user_id):
    """Fetch NID for a specific user for form auto-fill."""
    from accounts.models import User
    userobj = get_object_or_404(User, pk=user_id)
    return JsonResponse({
        'nid_number': userobj.nid_number or ""
    })
def api_get_party_details(request, party_id):
    """Fetch Name and Address for a specific business party."""
    party = get_object_or_404(BusinessParty, pk=party_id)
    return JsonResponse({
        'name': party.name,
        'address': party.address
    })
