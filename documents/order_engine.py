"""Automatic billing from client WhatsApp messages.

Each client conversation has at most one open order. Every new message from
that client is read and merged into it:

* item lines add items, or correct ones already ordered ("Rice 7 kg" after
  "Rice 5 kg" changes the quantity; "Cable clip 45 tk" adds the price);
* after we asked for company details, the next plain reply is taken as the
  buyer's name and address;
* once the bill has been sent for checking, a reply such as "OK" or
  "ঠিক আছে" approves it.

After each message the engine works out what is still missing, writes the
questions for the client, and - as soon as nothing is missing - creates the
bill (or updates it while the client has not approved it yet). It never
sends anything itself: the app shows the ready-written message and the
person using it taps send in WhatsApp.
"""
import difflib
import re
import urllib.parse
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from audit.utils import log_audit
from .models import BusinessParty, ClientOrder, InboxMessage, Product, Transaction, TransactionCategory
from .text_import import extract_items

AUTO_SOURCES = ('notification', 'share', 'paste')

_CONFIRM_WORDS = {
    'ok', 'okay', 'okk', 'okkk', 'oki', 'k', 'yes', 'yeah', 'yep', 'yup', 'confirm', 'confirmed', 'approve', 'approved',
    'done', 'correct', 'right', 'fine', 'perfect', 'thik', 'thikache', 'ache', 'ase', 'ji', 'jee', 'hmm', 'agreed',
    'ঠিক', 'আছে', 'হ্যাঁ', 'হা', 'জি', 'জ্বি', 'ওকে', 'কনফার্ম', 'ok👍', '👍', '✅', '👌',
}
_FILLER_WORDS = {
    'bhai', 'vai', 'bhaiya', 'sir', 'boss', 'please', 'pls', 'plz', 'send', 'go', 'ahead', 'all', 'is', 'it', 'its',
    'the', 'bill', 'invoice', 'thanks', 'thank', 'you', 'thx', 'dhonnobad', 'and', 'everything', 'looks', 'good',
    'ভাই', 'স্যার', 'ধন্যবাদ', 'পাঠান', 'বিল', 'সব',
}


# ── Reading replies ────────────────────────────────────────────────────────
def is_confirmation(text):
    words = re.findall(r'[\wঀ-৿]+|👍|✅|👌', (text or '').lower())
    if not words or re.search(r'\d', text or ''):
        return False
    return (any(w in _CONFIRM_WORDS for w in words)
            and all(w in _CONFIRM_WORDS or w in _FILLER_WORDS for w in words))


def _norm(text):
    return re.sub(r'\s+', ' ', (text or '').strip().lower())


def _same_item(a, b):
    """Is order item ``a`` the item a new line ``b`` talks about?"""
    if a.get('product') and a.get('product') == b.get('product'):
        return True
    x, y = _norm(a.get('description')), _norm(b.get('description'))
    if not x or not y:
        return False
    if x == y or (len(y) >= 4 and (y in x or x in y)):
        return True
    return difflib.SequenceMatcher(None, x, y).ratio() >= 0.85


def _num(value):
    try:
        return None if value in (None, '') else float(value)
    except (TypeError, ValueError):
        return None


# ── Matching items to saved products ───────────────────────────────────────
def _resolve(order, raw_items):
    """Attach saved products (unit, price) to freshly read items."""
    from .views import _quick_build_rows
    rows = _quick_build_rows(order.category, raw_items)
    out = []
    for raw, row in zip(raw_items, rows):
        from_message = _num(raw.get('unit_price'))
        out.append({
            'description': row['description'],
            'quantity': _num(raw.get('quantity')),
            'unit': row['unit'] if (raw.get('unit') or row['matched_product']) else None,
            'price': row['unit_price'] if row['unit_price'] else None,
            'price_from': 'message' if from_message else ('product' if row['unit_price'] else ''),
            'product': row['matched_product'],
        })
    return out


def _guess_category(order):
    if order.category_id:
        return
    names = [it['product'] for it in order.items if it.get('product')]
    tally = {}
    for cat_id in Product.objects.filter(name__in=names).values_list('category_id', flat=True):
        tally[cat_id] = tally.get(cat_id, 0) + 1
    if tally:
        order.category_id = max(tally, key=tally.get)
    elif order.party_id:
        cat = TransactionCategory.objects.filter(default_buyer_id=order.party_id).first()
        if cat:
            order.category = cat
    if not order.category_id and TransactionCategory.objects.count() == 1:
        order.category = TransactionCategory.objects.first()


def merge_items(order, new_items):
    """Add new items or update the ones already in the order. Returns True if anything changed."""
    changed = False
    items = list(order.items)
    for new in new_items:
        match = next((it for it in items if _same_item(it, new)), None)
        if match is None:
            if new.get('quantity') is None and not new.get('product'):
                continue                # a bare word that is not a known product: probably chatter
            items.append(dict(new, unit=new.get('unit') or 'Pcs'))
            changed = True
            continue
        for field in ('quantity', 'unit'):
            if new.get(field) not in (None, '') and new[field] != match.get(field):
                match[field] = new[field]
                changed = True
        if new.get('price_from') == 'message' and new.get('price') != match.get('price'):
            match['price'], match['price_from'] = new['price'], 'message'
            changed = True
        elif match.get('price') is None and new.get('price') is not None:
            match['price'], match['price_from'] = new['price'], new.get('price_from') or 'product'
            changed = True
    order.items = items
    return changed


# ── What is still missing ──────────────────────────────────────────────────
def client_questions(order):
    """Questions only the client can answer, in the order they will be asked."""
    questions = []
    for it in order.items:
        if it.get('quantity') is None:
            questions.append('How many *%s* do you need?' % it['description'])
    if order.ask_prices:
        for it in order.items:
            if it.get('price') is None:
                questions.append('What is the agreed rate for *%s* (per %s)?' % (it['description'], it.get('unit') or 'Pcs'))
    if not order.party_id and not order.buyer_name:
        questions.append('Please send your *company name and address* for the invoice.')
    return questions


def your_tasks(order):
    """Things the person using the app fills in directly."""
    tasks = []
    if not order.ask_prices and any(it.get('price') is None for it in order.items):
        tasks.append('price')
    if not order.category_id:
        tasks.append('category')
    return tasks


def is_complete(order):
    return bool(order.items) and not client_questions(order) and not your_tasks(order)


# ── The bill ───────────────────────────────────────────────────────────────
def _bill_payload(order):
    payload = {
        'category_id': order.category_id, 'mode': 'forward',
        'items': [{'description': it['description'], 'quantity': it['quantity'], 'unit': it.get('unit') or 'Pcs',
                   'price': it['price']} for it in order.items],
    }
    if order.party_id and order.party.party_type == 'BUYER':
        payload['buyer_id'] = order.party_id
    else:
        payload['buyer_name'] = order.buyer_name or order.title
    return payload


def _save_bill(order):
    """Create the bill, or bring it in line with the order while it is still a draft."""
    from .app_views import _build_bill
    from .category_rules import normalize_items
    from .models import TransactionItem
    from .views import _process_auto_product_saving

    bill = order.transaction
    if bill is None:
        bill = _build_bill(_bill_payload(order), None)
        if order.buyer_address and not order.party_id:
            bill.buyer_address = order.buyer_address
            bill.save()
        order.transaction = bill
        log_audit(None, 'Created Transaction',
                  'Bill #%s was created automatically from WhatsApp messages from %s.' % (bill.invoice_number, order.title),
                  transaction=bill)
        return
    if bill.status != 'DRAFT':
        return                           # approved bills are never changed behind anyone's back
    bill.items.all().delete()
    items = [TransactionItem(transaction=bill, description=it['description'], unit=it.get('unit') or 'Pcs',
                             quantity=Decimal(str(it['quantity'])), base_price=Decimal(str(it['price'])), sort_order=i)
             for i, it in enumerate(order.items)]
    normalize_items(bill, items)
    for item in items:
        item.save()
    _process_auto_product_saving(bill, items, user=None)
    bill.save()
    log_audit(None, 'Updated Transaction',
              'Bill #%s was updated from new WhatsApp messages from %s.' % (bill.invoice_number, order.title),
              transaction=bill)


def refresh(order, items_changed=True):
    """Re-check the order after a change and move it on if it can."""
    _guess_category(order)
    if order.status in (ClientOrder.NEEDS_INFO, ClientOrder.READY, ClientOrder.SENT):
        if is_complete(order):
            if items_changed or order.transaction_id is None:
                _save_bill(order)
            if order.status == ClientOrder.NEEDS_INFO or (order.status == ClientOrder.SENT and items_changed):
                order.status = ClientOrder.READY          # (re)send the bill for checking
        elif order.status != ClientOrder.NEEDS_INFO and items_changed:
            order.status = ClientOrder.NEEDS_INFO
    order.save()


# ── Messages arriving ──────────────────────────────────────────────────────
def _phone_for(sender, party):
    for value in ((party.whatsapp_number if party else ''), (party.contact if party else ''), sender):
        digits = re.sub(r'\D', '', value or '')
        if len(digits) >= 10:
            return digits
    return ''


def handle_message(msg):
    """Fold one inbox message into its client's order. Returns the order or None."""
    from .inbox_views import match_party

    key = (msg.chat or msg.sender or '').strip()
    if not key:
        return None
    order = ClientOrder.objects.filter(key=key, status__in=ClientOrder.OPEN).order_by('-id').first()
    raw, _skipped = extract_items(msg.text)
    strong = [r for r in raw if not r.get('weak')]

    if order is None:
        if not strong:
            return None                  # greetings and chatter do not start an order
        party = None if msg.chat else match_party(msg.sender)
        order = ClientOrder(key=key, title=key[:120], party=party,
                            phone=_phone_for('' if msg.chat else msg.sender, party))

    with db_transaction.atomic():
        if order.pk is None:
            order.save()
        msg.order = order
        msg.save(update_fields=['order'])

        if order.status in (ClientOrder.SENT, ClientOrder.CONFIRMED) and is_confirmation(msg.text):
            confirm(order, by_client=True)
            return order
        if order.status == ClientOrder.CONFIRMED:
            if not strong:
                return order             # "thanks" after confirming
            # New items after approval are a new order.
            order = ClientOrder.objects.create(key=key, title=order.title, party=order.party, phone=order.phone,
                                               buyer_name=order.buyer_name, buyer_address=order.buyer_address,
                                               category=order.category)
            msg.order = order
            msg.save(update_fields=['order'])

        resolved = _resolve(order, raw) if raw else []
        # After asking for company details, a reply that names no known
        # product is the answer ("Shop 4, Kadamtoli" is an address, not 4 shops).
        about_items = [r for r in resolved if r.get('product') or any(_same_item(it, r) for it in order.items)]
        if order.awaiting_buyer and not about_items and not is_confirmation(msg.text):
            lines = [l.strip() for l in msg.text.splitlines() if l.strip()]
            if lines:
                order.buyer_name = lines[0][:255]
                order.buyer_address = '\n'.join(lines[1:])
                order.awaiting_buyer = False
                refresh(order, items_changed=True)
            return order

        changed = merge_items(order, resolved) if resolved else False
        refresh(order, items_changed=changed)
    return order


def process_new_messages():
    """Fold every not-yet-read message into orders, oldest first."""
    handled = 0
    pending = InboxMessage.objects.filter(order__isnull=True, status='NEW',
                                          source__in=AUTO_SOURCES).order_by('received_at', 'id')
    for msg in pending:
        if handle_message(msg) is not None:
            handled += 1
    return handled


# ── Things the person using the app does ───────────────────────────────────
def confirm(order, by_client=False):
    order.status = ClientOrder.CONFIRMED
    order.confirmed_at = timezone.now()
    order.save()
    bill = order.transaction
    if bill is not None and bill.status in ('DRAFT', 'PRICED'):
        bill.status = 'APPROVED'
        bill.status_updated_at = timezone.now()
        bill.save(update_fields=['status', 'status_updated_at'])
        log_audit(None, 'Status Update', 'Bill #%s approved %s.' % (
            bill.invoice_number, 'by the client on WhatsApp' if by_client else 'after the client confirmed'),
            transaction=bill)


def set_prices(order, prices):
    """prices: {item index: price}"""
    changed = False
    for index, value in prices.items():
        price = _num(value)
        if price is not None and price >= 0 and 0 <= index < len(order.items):
            order.items[index]['price'] = price
            order.items[index]['price_from'] = 'you'
            changed = True
    refresh(order, items_changed=changed)


# ── Ready-written messages ─────────────────────────────────────────────────
def _whatsapp(phone, text):
    return 'https://wa.me/%s?text=%s' % (phone or '', urllib.parse.quote(text))


def ask_message(order):
    questions = client_questions(order)
    lines = ['Thank you for your order. To prepare your bill, please tell us:', '']
    lines += ['%d. %s' % (i, q) for i, q in enumerate(questions, start=1)]
    return '\n'.join(lines)


def check_message(order):
    from .app_views import client_whatsapp_text
    return client_whatsapp_text(order.transaction) + '\n\nReply *OK* to confirm, or tell us what to change.'


def deliver_caption(order, doc):
    bill = order.transaction
    number = bill.challan_number if doc == 'challan' else bill.invoice_number
    return '%s %s for your order. Thank you!' % (doc.title(), number or '')


def ask_url(order):
    return _whatsapp(order.phone, ask_message(order))


def check_url(order):
    return _whatsapp(order.phone, check_message(order))
