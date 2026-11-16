"""The phone app's own screens (everything under /app/).

These are designed for a phone from the start - bottom navigation, cards,
bottom sheets - rather than being the website's tables squeezed onto a small
screen. They sit on the same models and business rules as the website, so a
bill made here is the same bill everywhere.
"""
import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import permission_required
from audit.utils import log_audit
from .category_rules import normalize_items
from .inbox_views import (ingest_spool, inbox_context_for_bill, mark_messages_billed, match_party)
from .models import (BusinessParty, InboxMessage, Product, Transaction, TransactionCategory, TransactionItem)

STATUS_FLOW = ['DRAFT', 'PRICED', 'APPROVED', 'SUBMITTED', 'RELEASED']
STATUS_LABEL = dict(Transaction.STATUS_CHOICES)
THREE = Decimal('0.001')


def _can(user, perm):
    return user.is_superuser or getattr(user, 'role', '') == 'OWNER' or getattr(user, perm, False)


def _money(value):
    try:
        return float(Decimal(value).quantize(Decimal('0.01')))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def _bill_qs():
    return Transaction.objects.filter(is_deleted=False).select_related(
        'transaction_category').prefetch_related('items')


# ── Home ───────────────────────────────────────────────────────────────────
@login_required
def app_home(request):
    ingest_spool()
    now = timezone.localtime()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_bills = list(_bill_qs().filter(created_at__gte=month_start))
    by_status = dict(Transaction.objects.filter(is_deleted=False).values_list('status').annotate(n=Count('id')))
    hour = now.hour
    greeting = 'Good morning' if hour < 12 else ('Good afternoon' if hour < 17 else 'Good evening')
    return render(request, 'app/home.html', {
        'tab': 'home',
        'greeting': greeting,
        'month_name': now.strftime('%B'),
        'month_total': sum((b.grand_total for b in month_bills), Decimal('0')),
        'month_count': len(month_bills),
        'drafts': by_status.get('DRAFT', 0),
        'awaiting': by_status.get('PRICED', 0),
        'approved': by_status.get('APPROVED', 0) + by_status.get('SUBMITTED', 0),
        'new_messages': InboxMessage.objects.filter(status='NEW').count(),
        'recent': list(_bill_qs().order_by('-created_at')[:6]),
        'can_create': _can(request.user, 'p_create_bill'),
    })


# ── Bills ──────────────────────────────────────────────────────────────────
@login_required
def app_bills(request):
    q = (request.GET.get('q') or '').strip()
    status = (request.GET.get('status') or '').upper()
    bills = _bill_qs()
    if q:
        bills = bills.filter(Q(invoice_number__icontains=q) | Q(buyer_name__icontains=q)
                             | Q(supplier_name__icontains=q) | Q(items__description__icontains=q)).distinct()
    if status in STATUS_LABEL:
        bills = bills.filter(status=status)
    bills = list(bills.order_by('-created_at')[:150])
    counts = dict(Transaction.objects.filter(is_deleted=False).values_list('status').annotate(n=Count('id')))
    return render(request, 'app/bills.html', {
        'tab': 'bills', 'bills': bills, 'q': q, 'status': status,
        'statuses': [(s, STATUS_LABEL[s], counts.get(s, 0)) for s in STATUS_FLOW],
        'total_count': sum(counts.values()),
        'can_create': _can(request.user, 'p_create_bill'),
    })


@login_required
def app_bill_detail(request, pk):
    bill = get_object_or_404(_bill_qs(), pk=pk)
    items = list(bill.items.all())
    step = STATUS_FLOW.index(bill.status) if bill.status in STATUS_FLOW else 0
    next_status = STATUS_FLOW[step + 1] if step + 1 < len(STATUS_FLOW) else None
    charges = [
        ('Subtotal', bill.display_subtotal),
        ('Service charge', bill.total_service_charge),
        ('Discount', -bill.discount_amount),
        ('Duty', bill.total_duty),
        ('Tax', bill.total_tax),
        ('VAT', bill.total_vat),
    ]
    return render(request, 'app/bill_detail.html', {
        'tab': 'bills', 'bill': bill, 'items': items,
        'charges': [(label, value) for label, value in charges if label == 'Subtotal' or value],
        'steps': [(s, STATUS_LABEL[s], i <= step) for i, s in enumerate(STATUS_FLOW)],
        'next_status': next_status,
        'next_label': STATUS_LABEL.get(next_status),
        'can_edit': _can(request.user, 'p_edit_bill') or _can(request.user, 'p_create_bill'),
        'can_price': _can(request.user, 'p_price_bill'),
        'can_approve': _can(request.user, 'p_approve_bill'),
        'can_status': _can(request.user, 'p_edit_bill'),
        'can_delete': _can(request.user, 'p_delete_bill'),
    })


# ── New bill ───────────────────────────────────────────────────────────────
@login_required
@permission_required('p_create_bill')
def app_bill_new(request):
    context = {
        'tab': 'new',
        'categories': TransactionCategory.objects.filter(is_deleted=False).order_by('name'),
        'buyers': BusinessParty.objects.filter(is_deleted=False, party_type='BUYER').order_by('name'),
        'suppliers': BusinessParty.objects.filter(is_deleted=False, party_type='SUPPLIER').order_by('name'),
    }
    if request.GET.get('inbox'):
        context.update(inbox_context_for_bill(request.GET['inbox']))
    return render(request, 'app/bill_new.html', context)


def _decimal(value, default=None):
    try:
        if value in (None, ''):
            return default
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def _party_fields(party):
    return (party.name, party.bin_number, party.address, party.contact)


def _build_bill(data, user):
    """Create a draft bill from the composer's simple payload.

    Applies the same rules the website form does: category defaults for the
    tax rates, the next invoice/challan number, category item rules, and
    saving new items to the product list.
    """
    from .views import _get_next_serial_number, _process_auto_product_saving

    category = TransactionCategory.objects.filter(pk=data.get('category_id') or 0).first()
    if category is None:
        raise ValueError('Choose a category.')
    rows = []
    for raw in data.get('items') or []:
        description = str(raw.get('description') or '').strip()
        if not description:
            continue
        quantity = _decimal(raw.get('quantity'), Decimal('1'))
        price = _decimal(raw.get('price'), Decimal('0'))
        if quantity <= 0:
            raise ValueError('Quantity for "%s" must be more than zero.' % description)
        if price < 0:
            raise ValueError('Price for "%s" cannot be negative.' % description)
        rows.append((description[:255], (str(raw.get('unit') or '').strip() or 'Pcs')[:50], quantity, price))
    if not rows:
        raise ValueError('Add at least one item.')

    mode = 'reverse' if data.get('mode') == 'reverse' else 'forward'
    seq = _get_next_serial_number(category)
    stamp = timezone.now().strftime('%Y%m')
    prefix = category.invoice_prefix or 'INV'

    bill = Transaction(
        transaction_category=category, status='DRAFT', calculation_mode=mode,
        invoice_number='%s-%s-%04d' % (prefix, stamp, seq),
        challan_number='CHL-%s-%s-%04d' % (prefix, stamp, seq),
        vat_percentage=category.default_vat, tax_percentage=category.default_tax,
        duty_percentage=category.default_duty, service_charge_percentage=category.default_service_charge,
        profit_margin=category.default_profit_margin,
    )
    supplier = (BusinessParty.objects.filter(pk=data.get('supplier_id') or 0).first() or category.default_supplier)
    if supplier:
        bill.supplier_name, bill.supplier_bin, bill.supplier_address, bill.supplier_contact = _party_fields(supplier)
    buyer = BusinessParty.objects.filter(pk=data.get('buyer_id') or 0).first()
    typed_buyer = str(data.get('buyer_name') or '').strip()
    if buyer is None and not typed_buyer:
        buyer = category.default_buyer
    if buyer:
        bill.buyer_name, bill.buyer_bin, bill.buyer_address, bill.buyer_contact = _party_fields(buyer)
    else:
        bill.buyer_name = typed_buyer[:255]
    bill.save()

    items = [TransactionItem(transaction=bill, description=d, unit=u, quantity=q, base_price=p, sort_order=i)
             for i, (d, u, q, p) in enumerate(rows)]
    normalize_items(bill, items)
    for item in items:
        item.save()

    if mode == 'reverse':
        # The prices typed are what the customer pays, taxes included. Every
        # charge scales with the base price, so one factor converts them all.
        target = sum((q * p for _d, _u, q, p in rows), Decimal('0'))
        gross = Transaction.objects.get(pk=bill.pk).grand_total
        if target > 0 and gross > 0:
            factor = target / gross
            for item in items:
                item.base_price = (item.base_price * factor).quantize(THREE)
                item.save()
    _process_auto_product_saving(bill, items, user=user)
    return Transaction.objects.get(pk=bill.pk)


def _totals(bill):
    return {
        'subtotal': _money(bill.display_subtotal),
        'service_charge': _money(bill.total_service_charge),
        'duty': _money(bill.total_duty),
        'tax': _money(bill.total_tax),
        'vat': _money(bill.total_vat),
        'grand_total': _money(bill.grand_total),
        'invoice_number': bill.invoice_number,
    }


class _Rollback(Exception):
    pass


@login_required
@permission_required('p_create_bill')
@require_POST
def app_bill_save(request):
    """Save the composer's bill, or with ``preview`` just report its totals."""
    try:
        data = json.loads(request.body or '{}')
    except (ValueError, TypeError):
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    if data.get('preview'):
        totals = {}
        try:
            with db_transaction.atomic():
                totals = _totals(_build_bill(data, request.user))
                raise _Rollback        # a preview must leave nothing behind
        except _Rollback:
            return JsonResponse({'ok': True, 'totals': totals})
        except ValueError as exc:
            return JsonResponse({'ok': False, 'error': str(exc)})

    try:
        with db_transaction.atomic():
            bill = _build_bill(data, request.user)
            billed = mark_messages_billed(str(data.get('inbox_ids') or ''), bill)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    detail = 'Created new DRAFT Bill #%s under category %s with %d line item(s) from the app.' % (
        bill.invoice_number, bill.transaction_category.name, bill.items.count())
    if billed:
        detail += ' Built from %d inbox message(s).' % billed
    log_audit(request.user, 'Created Transaction', detail, transaction=bill)
    messages.success(request, 'Bill %s saved as a draft.' % bill.invoice_number)
    return JsonResponse({'ok': True, 'id': bill.pk, 'url': '/app/bills/%d/' % bill.pk})


# ── Inbox ──────────────────────────────────────────────────────────────────
@login_required
@permission_required('p_create_bill')
def app_inbox(request):
    ingest_spool()
    status = (request.GET.get('status') or 'NEW').upper()
    if status not in ('NEW', 'BILLED', 'ARCHIVED'):
        status = 'NEW'
    msgs = list(InboxMessage.objects.filter(status=status).select_related('transaction')[:400])
    groups, index = [], {}
    for m in msgs:
        key = m.chat or m.sender or 'Unknown sender'
        if key not in index:
            index[key] = {'title': key, 'is_group': bool(m.chat), 'messages': [], 'latest': m.received_at}
            groups.append(index[key])
        index[key]['messages'].append(m)
    for g in groups:
        g['messages'].reverse()
        g['ids'] = ','.join(str(m.pk) for m in g['messages'])
        g['party'] = None if g['is_group'] else match_party(g['title'])
        g['initial'] = (g['title'].strip()[:1] or '?').upper()
    counts = dict(InboxMessage.objects.values_list('status').annotate(n=Count('id')))
    return render(request, 'app/inbox.html', {
        'tab': 'inbox', 'groups': groups, 'status': status,
        'counts': {'NEW': counts.get('NEW', 0), 'BILLED': counts.get('BILLED', 0),
                   'ARCHIVED': counts.get('ARCHIVED', 0)},
    })


# ── Products, contacts, more ───────────────────────────────────────────────
@login_required
def app_products(request):
    q = (request.GET.get('q') or '').strip()
    products = Product.objects.filter(is_deleted=False).select_related('category')
    if q:
        products = products.filter(Q(name__icontains=q) | Q(category__name__icontains=q))
    return render(request, 'app/products.html', {
        'tab': 'more', 'q': q, 'products': list(products.order_by('name')[:300]),
        'total': Product.objects.filter(is_deleted=False).count(),
    })


@login_required
def app_contacts(request):
    q = (request.GET.get('q') or '').strip()
    parties = BusinessParty.objects.filter(is_deleted=False)
    if q:
        parties = parties.filter(Q(name__icontains=q) | Q(contact__icontains=q) | Q(whatsapp_number__icontains=q))
    parties = list(parties.order_by('name')[:300])
    for p in parties:
        p.initial = (p.name.strip()[:1] or '?').upper()
    return render(request, 'app/contacts.html', {
        'tab': 'more', 'q': q, 'parties': parties,
        'can_manage': _can(request.user, 'p_manage_contacts'),
    })


@login_required
def app_more(request):
    u = request.user
    return render(request, 'app/more.html', {
        'tab': 'more',
        'can_summary': _can(u, 'p_view_bill_summary'),
        'can_categories': _can(u, 'p_manage_categories'),
        'can_contacts': _can(u, 'p_manage_contacts'),
        'can_users': _can(u, 'p_manage_users'),
        'can_audit': _can(u, 'p_approve_bill'),
        'can_authorizations': _can(u, 'p_manage_authorizations'),
        'can_gate': _can(u, 'p_view_gate_entry'),
        'can_backup': u.is_superuser or getattr(u, 'role', '') == 'OWNER',
    })
