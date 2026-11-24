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


def client_phone(bill):
    phone = ''.join(ch for ch in (bill.buyer_contact or '') if ch.isdigit())
    if phone.startswith('0') and len(phone) == 11:
        phone = '88' + phone
    return phone


def client_whatsapp_url(bill):
    import urllib.parse
    return 'https://wa.me/%s?text=%s' % (client_phone(bill), urllib.parse.quote(client_whatsapp_text(bill)))


def client_whatsapp_text(bill):
    """A WhatsApp message for the customer: what they bought and what they owe.

    Deliberately not Transaction.get_whatsapp_message(), which is the staff
    message (base prices, margin, VAT rates, links into the system).
    """
    lines = ['*%s*' % (bill.supplier_name or 'Bill'),
             'Invoice: %s' % (bill.invoice_number or '-'),
             'Date: %s' % timezone.localtime(bill.created_at).strftime('%d %b %Y'), '']
    for i, item in enumerate(bill.items.all(), start=1):
        lines.append('%d. %s - %s %s x %s = Tk %s' % (
            i, item.display_description, ('%f' % item.quantity).rstrip('0').rstrip('.'), item.unit,
            '{:,.2f}'.format(item.billed_unit_price), '{:,.2f}'.format(item.billed_total)))
    lines += ['', '*Total payable: Tk %s*' % '{:,.2f}'.format(bill.grand_total)]
    return '\n'.join(lines)


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
        'tab': 'bills', 'bill': bill, 'items': items, 'client_wa_url': client_whatsapp_url(bill),
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
    from .models import ClientOrder
    ingest_spool()
    status = (request.GET.get('status') or 'NEW').upper()
    if status not in ('NEW', 'BILLED', 'ARCHIVED'):
        status = 'NEW'
    msgs = list(InboxMessage.objects.filter(status=status, order__isnull=True).select_related('transaction')[:400])
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
    counts = dict(InboxMessage.objects.filter(order__isnull=True).values_list('status').annotate(n=Count('id')))
    open_orders = list(ClientOrder.objects.filter(status__in=ClientOrder.OPEN).select_related('transaction', 'party', 'category'))
    done = list(ClientOrder.objects.filter(status__in=(ClientOrder.DELIVERED,)).select_related('transaction')[:15])
    from . import ai_client
    return render(request, 'app/inbox.html', {
        'tab': 'inbox', 'groups': groups, 'status': status, 'ai_on': ai_client.is_configured(),
        'orders': _order_cards(open_orders), 'done_orders': done,
        'buyers': BusinessParty.objects.filter(is_deleted=False, party_type='BUYER').order_by('name'),
        'categories': TransactionCategory.objects.filter(is_deleted=False).order_by('name'),
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


# ── Live server sync ───────────────────────────────────────────────────────
def _is_owner(user):
    return user.is_superuser or getattr(user, 'role', '') == 'OWNER'


@login_required
def app_sync(request):
    """Connect this phone to the live website and keep the two in step."""
    from django.conf import settings
    from . import sync_client

    if not getattr(settings, 'MOBILE_APP', False):
        messages.info(request, 'Live-server sync is set up from the phone app.')
        return redirect('app_more')
    if not _is_owner(request.user):
        messages.error(request, 'Only the owner can manage live-server sync.')
        return redirect('app_more')

    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'connect':
                sync_client.connect(request.POST.get('server'), request.POST.get('username'),
                                    request.POST.get('password'))
                # The accounts on this phone are now the live site's accounts.
                request.session.flush()
                messages.success(request, 'Connected. This phone now holds a copy of your live data. '
                                          'Sign in with your website username and password.')
                return redirect('login')
            if action == 'sync':
                result = sync_client.sync_now()
                if result.get('busy'):
                    messages.info(request, 'A sync is already running.')
                else:
                    messages.success(request, 'Synced: sent %d change(s), received %d.'
                                     % (result.get('sent', 0), result.get('received', 0)))
            elif action == 'dismiss':
                sync_client.dismiss_problems()
            elif action == 'disconnect':
                sync_client.disconnect()
                messages.success(request, 'Disconnected. Your data is still on this phone.')
        except sync_client.SyncError as exc:
            messages.error(request, str(exc))
        return redirect('app_sync')

    state = sync_client.status()
    last = None
    if state.get('last_sync'):
        from django.utils.dateparse import parse_datetime
        last = parse_datetime(state['last_sync'])
    return render(request, 'app/sync.html', {
        'tab': 'more', 'state': state, 'last_sync': last,
        'default_server': getattr(settings, 'LIVE_SERVER_URL', ''),
    })


@login_required
def app_sync_status(request):
    from django.conf import settings
    from . import sync_client
    if not getattr(settings, 'MOBILE_APP', False):
        return JsonResponse({'connected': False})
    state = sync_client.status()
    return JsonResponse({'connected': state['connected'], 'pending': state['pending'],
                         'last_sync': state['last_sync'], 'error': bool(state['last_error']),
                         'problems': len(state['problems'])})


# ── Client orders (automatic billing from WhatsApp) ────────────────────────
def _order_cards(orders):
    from . import order_engine
    cards = []
    for order in orders:
        cards.append({
            'order': order,
            'questions': order_engine.client_questions(order),
            'tasks': order_engine.your_tasks(order),
            'items': [dict(it, index=i, total=(it['quantity'] or 0) * (it['price'] or 0))
                      for i, it in enumerate(order.items)],
            'ask_text': order_engine.ask_message(order),
            'messages': list(order.messages.order_by('received_at', 'id')),
            'initial': (order.title.strip()[:1] or '?').upper(),
            'outbox': list(order.outgoing.filter(status__in=('PENDING', 'FAILED'))),
            'staff_questions': _open_staff_questions(order),
        })
    return cards


def _open_staff_questions(order):
    last_answer = order.events.filter(kind='owner').order_by('-seq').values_list('seq', flat=True).first() or 0
    return list(order.events.filter(kind='owner_question', seq__gt=last_answer).order_by('seq'))


@login_required
@permission_required('p_create_bill')
@require_POST
def app_order_action(request, pk):
    """Buttons on an order card. "Send" actions mark the step done and open
    WhatsApp with the message ready; the user taps send there."""
    from django.http import HttpResponseRedirect
    from . import order_engine
    from .models import ClientOrder

    from . import ledger, whatsapp
    from .models import OutgoingMessage
    order = get_object_or_404(ClientOrder, pk=pk)
    ledger.ensure_order_ledger(order)
    action = request.POST.get('action')
    back = redirect('/app/inbox/#order-%d' % order.pk)
    user = request.user

    def sent_by_hand(text, document=''):
        msg = OutgoingMessage.objects.create(order=order, to_phone=order.phone, text=text, document=document,
                                             created_by='staff')
        whatsapp.mark_sent(msg, 'manual', user=user)

    if action == 'ask':
        if order_engine.client_questions(order):
            order.awaiting_buyer = any('company name' in q for q in order_engine.client_questions(order))
            order.asked_at, order.asked_text = timezone.now(), order_engine.ask_message(order)
            order.save()
            sent_by_hand(order.asked_text)
            return HttpResponseRedirect(order_engine.ask_url(order))
    elif action == 'prices':
        prices = {}
        for key, value in request.POST.items():
            if key.startswith('price_') and key[6:].isdigit() and value.strip():
                prices[int(key[6:])] = value
        order_engine.set_prices(order, prices, user=user)
    elif action == 'ask_prices':
        order.ask_prices = not order.ask_prices
        order_engine.refresh(order, items_changed=False)
    elif action == 'buyer':
        party = BusinessParty.objects.filter(pk=request.POST.get('party_id') or 0).first()
        if party:
            order.party = party
            if not order.phone:
                order.phone = order_engine._phone_for('', party)
        elif request.POST.get('buyer_name', '').strip():
            order.buyer_name = request.POST['buyer_name'].strip()[:255]
        ledger.record(order, 'owner', 'staff', 'Staff set the buyer: %s' % (party.name if party else order.buyer_name),
                      {}, user=user)
        order_engine.refresh(order, items_changed=True)
    elif action == 'category':
        order.category = TransactionCategory.objects.filter(pk=request.POST.get('category_id') or 0).first()
        ledger.record(order, 'owner', 'staff', 'Staff set the category: %s' % (order.category.name if order.category else '-'),
                      {}, user=user)
        order_engine.refresh(order, items_changed=True)
    elif action == 'phone':
        order.phone = ''.join(ch for ch in request.POST.get('phone', '') if ch.isdigit())[:20]
        if order.phone.startswith('0') and len(order.phone) == 11:
            order.phone = '88' + order.phone
        order.save()
        ledger.record(order, 'owner', 'staff', "Staff saved the client's WhatsApp number: %s" % order.phone, {}, user=user)
    elif action == 'send_check' and order.transaction_id:
        order.status, order.sent_at = ClientOrder.SENT, timezone.now()
        order.save()
        ledger.new_bill_version(order, 'staff', user=user)
        sent_by_hand(order_engine.check_message(order))
        return HttpResponseRedirect(order_engine.check_url(order))
    elif action == 'confirm':
        order_engine.confirm(order, user=user)
    elif action == 'deliver' and order.transaction_id:
        doc = request.POST.get('doc') if request.POST.get('doc') in ('invoice', 'challan', 'quotation') else 'invoice'
        if doc not in order.delivered:
            order.delivered = order.delivered + [doc]
        if doc == 'invoice':
            order.status = ClientOrder.DELIVERED
        order.save()
        number = order.transaction.challan_number if doc == 'challan' else order.transaction.invoice_number
        ledger.record(order, 'document_sent', 'staff', '%s %s sent to the client (bill version %d, code %s).'
                      % (doc.title(), number, order.bill_version, order.version_code or '-'),
                      {'document': doc, 'number': number, 'version': order.bill_version,
                       'bill': ledger.bill_snapshot(order)}, user=user)
        OutgoingMessage.objects.filter(order=order, document=doc, status=OutgoingMessage.PENDING).update(
            status=OutgoingMessage.SENT, sent_at=timezone.now(), channel='manual')
        import urllib.parse
        query = urllib.parse.urlencode({'send': 'whatsapp', 'phone': order.phone,
                                        'caption': order_engine.deliver_caption(order, doc),
                                        'back': '/app/inbox/#order-%d' % order.pk})
        return redirect('/bills/%d/print/%s/?%s' % (order.transaction_id, doc, query))
    elif action == 'cancel':
        order.status = ClientOrder.CANCELLED
        order.save()
        ledger.record(order, 'owner', 'staff', 'Staff cancelled the order.', {}, user=user)
        messages.info(request, 'Order from %s cancelled. Its messages stay in the inbox.' % order.title)
        order.messages.update(order=None, status='ARCHIVED')
    elif action == 'reopen':
        order.status = ClientOrder.NEEDS_INFO
        ledger.record(order, 'owner', 'staff', 'Staff reopened the order.', {}, user=user)
        order_engine.refresh(order, items_changed=True)
    elif action in ('approve', 'send_by_hand', 'discard'):
        msg = get_object_or_404(OutgoingMessage, pk=request.POST.get('message_id') or 0, order=order,
                                status__in=(OutgoingMessage.PENDING, OutgoingMessage.FAILED))
        if action == 'discard':
            msg.status = OutgoingMessage.CANCELLED
            msg.save(update_fields=['status'])
            ledger.record(order, 'owner', 'staff', 'Staff discarded a message to the %s: %s'
                          % ('staff' if msg.to_staff else 'client', msg.text), {'outgoing': msg.pk}, user=user)
        elif msg.document and not (msg.file and whatsapp.is_configured() and msg.to_phone):
            return redirect_to_document(order, msg.document)
        elif action == 'approve' and whatsapp.is_configured() and msg.to_phone:
            try:
                whatsapp.dispatch(msg, user=user)
                messages.success(request, 'Sent.')
            except whatsapp.SendError as exc:
                messages.error(request, str(exc))
        else:
            whatsapp.mark_sent(msg, 'manual', user=user)
            import urllib.parse
            return HttpResponseRedirect('https://wa.me/%s?text=%s' % (msg.to_phone, urllib.parse.quote(msg.text)))
    elif action == 'answer':
        answer = request.POST.get('answer', '').strip()
        if answer:
            ledger.record(order, 'owner', 'staff', 'Staff answered: %s' % answer,
                          {'question': request.POST.get('question_seq')}, user=user)
            run_assistant_later(order)
    elif action == 'resume':
        order.paused, order.paused_reason = False, ''
        order.save(update_fields=['paused', 'paused_reason', 'updated_at'])
        ledger.record(order, 'owner', 'staff', 'Staff handed the conversation back to the assistant.', {}, user=user)
        run_assistant_later(order)
    elif action == 'pause':
        order.paused, order.paused_reason = True, 'Taken over by staff'
        order.save(update_fields=['paused', 'paused_reason', 'updated_at'])
        ledger.record(order, 'handoff', 'staff', 'Staff took over the conversation.', {}, user=user)
    return back


def redirect_to_document(order, doc):
    import urllib.parse
    from . import order_engine
    query = urllib.parse.urlencode({'send': 'whatsapp', 'phone': order.phone,
                                    'caption': order_engine.deliver_caption(order, doc),
                                    'back': '/app/inbox/#order-%d' % order.pk})
    order.delivered = list(set(order.delivered + [doc]))
    if doc == 'invoice':
        from .models import ClientOrder
        order.status = ClientOrder.DELIVERED
    order.save()
    from . import ledger
    number = order.transaction.challan_number if doc == 'challan' else order.transaction.invoice_number
    ledger.record(order, 'document_sent', 'staff', '%s %s sent to the client (bill version %d, code %s).'
                  % (doc.title(), number, order.bill_version, order.version_code or '-'),
                  {'document': doc, 'number': number, 'version': order.bill_version, 'bill': ledger.bill_snapshot(order)})
    from .models import OutgoingMessage
    OutgoingMessage.objects.filter(order=order, document=doc, status=OutgoingMessage.PENDING).update(
        status=OutgoingMessage.SENT, sent_at=timezone.now(), channel='manual')
    return redirect('/bills/%d/print/%s/?%s' % (order.transaction_id, doc, query))


def run_assistant_later(order):
    """Let the AI assistant act on the order in the background (if it is set up)."""
    import threading
    from . import ai_client, app_settings, order_agent
    if not ai_client.is_configured() or not app_settings.assistant_on():
        return

    def work(pk):
        from django.db import close_old_connections
        from .models import ClientOrder
        try:
            order_agent.run(ClientOrder.objects.get(pk=pk))
        except Exception:
            import logging
            logging.getLogger(__name__).exception('assistant run failed')
        finally:
            close_old_connections()
    threading.Thread(target=work, args=(order.pk,), daemon=True).start()


# ── AI costs, setup, and the order record ──────────────────────────────────
@login_required
def app_ai_costs(request):
    """What the AI assistant has cost: today, this month, by order and by purpose."""
    from datetime import timedelta
    from decimal import Decimal
    from django.db.models import Sum
    from . import ai_client
    from .models import AIUsage

    if not _is_owner(request.user):
        messages.error(request, 'Only the owner can see AI costs.')
        return redirect('app_more')
    rate = ai_client.usd_to_bdt()
    now = timezone.localtime()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = today.replace(day=1)
    sums = ('cost_usd', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')

    def total(qs):
        agg = qs.aggregate(**{f: Sum(f) for f in sums})
        usd = agg['cost_usd'] or Decimal('0')
        return {'usd': usd, 'bdt': usd * rate, 'calls': qs.count(),
                'tokens': sum((agg[f] or 0) for f in sums[1:])}

    periods = [('Today', total(AIUsage.objects.filter(created_at__gte=today))),
               ('Last 7 days', total(AIUsage.objects.filter(created_at__gte=today - timedelta(days=6)))),
               ('This month', total(AIUsage.objects.filter(created_at__gte=month))),
               ('All time', total(AIUsage.objects.all()))]
    days = []
    for i in range(13, -1, -1):
        start = today - timedelta(days=i)
        t = total(AIUsage.objects.filter(created_at__gte=start, created_at__lt=start + timedelta(days=1)))
        days.append({'day': start, 'usd': t['usd'], 'bdt': t['bdt']})
    peak = max([d['usd'] for d in days] + [Decimal('0.0001')])
    for d in days:
        d['pct'] = int(d['usd'] / peak * 100)
    by_order = (AIUsage.objects.filter(order__isnull=False).values('order_id', 'order__title', 'order__transaction__invoice_number')
                .annotate(usd=Sum('cost_usd')).order_by('-usd')[:20])
    by_order = [dict(r, bdt=r['usd'] * rate) for r in by_order]
    by_client = (AIUsage.objects.filter(order__isnull=False).values('order__key').annotate(usd=Sum('cost_usd'))
                 .order_by('-usd')[:10])
    by_client = [dict(r, bdt=r['usd'] * rate) for r in by_client]
    delivered = AIUsage.objects.filter(order__status='DELIVERED').values('order_id').distinct().count()
    spent_delivered = AIUsage.objects.filter(order__status='DELIVERED').aggregate(s=Sum('cost_usd'))['s'] or Decimal('0')
    return render(request, 'app/ai_costs.html', {
        'tab': 'more', 'periods': periods, 'days': days, 'by_order': by_order, 'by_client': by_client,
        'per_order_bdt': (spent_delivered / delivered * rate) if delivered else None,
        'recent': AIUsage.objects.select_related('order')[:30], 'rate': rate,
        'model': ai_client.model(), 'configured': ai_client.is_configured(),
        'prices': ai_client.PRICES.get(ai_client.model()),
    })


@login_required
def app_setup(request):
    """Which connections are set up, and what is still a placeholder."""
    from django.conf import settings
    from . import ai_client, app_settings, pdf_render, whatsapp
    from .models import AppSetting
    if not _is_owner(request.user):
        messages.error(request, 'Only the owner can see the setup.')
        return redirect('app_more')

    if request.method == 'POST':
        def num(name, default, cast):
            try:
                return cast(request.POST.get(name, default))
            except (TypeError, ValueError):
                return default
        AppSetting.put('assistant_on', request.POST.get('assistant_on') == 'on')
        AppSetting.put('autopilot_on', request.POST.get('autopilot_on') == 'on')
        AppSetting.put('autopilot_max_total', max(0.0, num('max_total', app_settings.max_total(), float)))
        AppSetting.put('autopilot_trusted_after', max(0, num('trusted_after', app_settings.trusted_after(), int)))
        AppSetting.put('pause_on_staff_reply', request.POST.get('pause_on_staff_reply') == 'on')
        if request.POST.get('quiet_on') == 'on':
            AppSetting.put('quiet_hours', [num('quiet_start', 22, int) % 24, num('quiet_end', 8, int) % 24])
        else:
            AppSetting.put('quiet_hours', [])
        from audit.utils import log_audit
        log_audit(request.user, 'Settings', 'Assistant %s, autopilot %s.' % (
            'on' if app_settings.assistant_on() else 'off', 'on' if app_settings.autopilot_on() else 'off'))
        messages.success(request, 'Saved. Changes apply straight away.')
        return redirect('app_setup')

    def state(name):
        value = (getattr(settings, name, '') or '')
        value = str(value).strip()
        return bool(value) and not value.startswith('PASTE')

    checks = [
        ('Claude API key', 'ANTHROPIC_API_KEY', state('ANTHROPIC_API_KEY'),
         'Reads messages and photos and talks to clients. Get it at console.anthropic.com → API keys.'),
        ('WhatsApp access token', 'WHATSAPP_TOKEN', state('WHATSAPP_TOKEN'),
         'Meta Business → System users → generate a permanent token with WhatsApp permissions.'),
        ('WhatsApp phone number ID', 'WHATSAPP_PHONE_NUMBER_ID', state('WHATSAPP_PHONE_NUMBER_ID'),
         'developers.facebook.com → your app → WhatsApp → API setup.'),
        ('WhatsApp app secret', 'WHATSAPP_APP_SECRET', state('WHATSAPP_APP_SECRET'),
         'App settings → Basic → App secret. Used to check messages really come from WhatsApp.'),
        ('Webhook verify token', 'WHATSAPP_VERIFY_TOKEN', state('WHATSAPP_VERIFY_TOKEN'),
         'Any phrase you choose; type the same phrase in Meta when adding the webhook.'),
        ('Your WhatsApp number', 'OWNER_WHATSAPP', state('OWNER_WHATSAPP'),
         'Where the assistant sends you questions and alerts, e.g. 8801711000000.'),
        ('Reopening template', 'WHATSAPP_REOPEN_TEMPLATE', state('WHATSAPP_REOPEN_TEMPLATE'),
         'Name of an approved Utility template (variables: {{1}} client name, {{2}} order or bill number). '
         'Used automatically when more than 24 hours have passed since the client last wrote.'),
    ]
    return render(request, 'app/setup.html', {
        'tab': 'more', 'checks': checks, 'model': ai_client.model(),
        'webhook_url': request.build_absolute_uri('/api/whatsapp/webhook/'),
        'assistant_on': app_settings.assistant_on(), 'autopilot': app_settings.autopilot_on(),
        'max_total': app_settings.max_total(), 'trusted_after': app_settings.trusted_after(),
        'quiet': app_settings.quiet_hours(), 'pause_on_staff_reply': app_settings.pause_on_staff_reply(),
        'pdf_ready': pdf_render.available(),
        'ai_ready': ai_client.is_configured(), 'wa_ready': whatsapp.is_configured(),
    })


@login_required
def app_order_record(request, pk):
    """The order's full, tamper-evident record - printable as proof."""
    from . import ledger
    from .models import ClientOrder
    order = get_object_or_404(ClientOrder.objects.select_related('transaction', 'party'), pk=pk)
    ok, verdict = ledger.verify(order)
    events = list(order.events.select_related('user'))
    head = events[-1].hash if events else ''
    return render(request, 'app/order_record.html', {
        'tab': 'inbox', 'order': order, 'events': events, 'chain_ok': ok, 'verdict': verdict,
        'head': head, 'printed': timezone.localtime(),
    })
