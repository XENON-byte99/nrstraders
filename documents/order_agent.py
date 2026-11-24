"""The AI order assistant: Claude talks to the client until the bill is agreed.

Each time something new arrives for an order (a client message or photo, or
an answer from staff), the assistant gets the order's full record and
decides what to do through a fixed set of tools: change items, set the buyer,
ask the client, ask staff, show the bill, record the client's confirmation,
send documents, or hand over to a person.

Rules it cannot break, because the code enforces them, not the prompt:
* prices come from the client's message, the saved product list or staff -
  never from the assistant;
* every total, VAT and tax figure the client sees is calculated and written
  by the billing code;
* a confirmation only counts for the exact bill version the client was last
  shown, and only after a client message that came in after it;
* every message, change and decision goes into the order's permanent record.
"""
import base64
import json
import logging
import threading

from django.conf import settings
from django.utils import timezone

from . import ai_client, ledger, order_engine, whatsapp
from .models import ClientOrder, InboxMessage, OrderEvent, OutgoingMessage, Product, TransactionCategory

logger = logging.getLogger(__name__)
MAX_STEPS = 10
MAX_IMAGES = 8
_locks = {}
_locks_guard = threading.Lock()


def _lock_for(order_id):
    with _locks_guard:
        return _locks.setdefault(order_id, threading.Lock())


SYSTEM_PROMPT = """You are the order assistant for a supply company in Bangladesh. Clients send their orders on WhatsApp as text, photos of handwritten or printed lists, or both. Your job is to turn each conversation into a complete, correct bill that the client confirms, while the company's staff stay in control.

How you work:
- Read everything in the order record, including photos. Record items with update_items, the buyer with set_bill_details.
- Prices: use a price only if the client wrote it, staff gave it, or the system fills it from saved products. Never estimate a price. If an item has no price and the client did not state one, ask staff with ask_staff (kind "price"). Ask the client about prices only if the record shows this client sets their own rates.
- If a written price may include VAT and tax ("with VAT", "final", "net", "সহ"), or prices look like final prices, mark them price_is_final. If you cannot tell, ask the client once whether their prices include VAT and tax.
- When something is unreadable or unclear, ask the client about exactly that part and keep everything you did understand. Quote what you could read. Put all open questions in one message, numbered.
- Before the client confirms, show the bill with show_bill. Never write totals, VAT or prices yourself in messages; show_bill sends the figures.
- A confirmation counts only when the client clearly agrees after seeing the latest bill version. Then call record_confirmation with their exact words, and send_documents for the invoice and challan.
- If the client changes anything after a bill was shown, update the order and show the bill again.
- Hand over to staff with hand_over when: the client asks for a person, is upset or complains, asks for a discount or credit terms, asks about anything other than this order, or the same question stays unanswered after you asked it twice.
- Never promise delivery dates, stock or payment terms. Say staff will confirm.
- Write to clients briefly and politely, in the language they use (Bangla, English or Banglish). One message per turn at most.
- If there is nothing to do (for example the client said "thanks" after delivery), do nothing.
"""

TOOLS = [
    {
        "name": "update_items",
        "description": "Add, change or remove order items. Prices you pass must come from the client's own words or a staff answer. The result shows the order after the change, with prices filled from saved products and any warnings.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["action", "item_number", "description", "quantity", "unit", "price",
                                     "price_is_final", "source"],
                        "properties": {
                            "action": {"type": "string", "enum": ["add", "update", "remove"]},
                            "item_number": {"type": ["integer", "null"], "description": "Existing item number for update/remove"},
                            "description": {"type": ["string", "null"]},
                            "quantity": {"type": ["number", "null"]},
                            "unit": {"type": ["string", "null"]},
                            "price": {"type": ["number", "null"], "description": "Per-unit price, only if stated by the client or staff"},
                            "price_is_final": {"type": ["boolean", "null"], "description": "True if the price includes VAT and tax"},
                            "source": {"type": "string", "description": "Where this came from, e.g. 'photo 2, line 4' or 'message 10:42'"},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "set_bill_details",
        "description": "Set the buyer's details, the category, or whether this client's prices include VAT. Pass null for anything unchanged.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["buyer_name", "buyer_address", "buyer_bin", "category", "prices_include_vat"],
            "properties": {
                "buyer_name": {"type": ["string", "null"]},
                "buyer_address": {"type": ["string", "null"]},
                "buyer_bin": {"type": ["string", "null"]},
                "category": {"type": ["string", "null"], "description": "One of the listed category names"},
                "prices_include_vat": {"type": ["boolean", "null"]},
            },
        },
    },
    {
        "name": "message_client",
        "description": "Send one WhatsApp message to the client (questions, acknowledgements). Do not include prices or totals; use show_bill for those.",
        "strict": True,
        "input_schema": {
            "type": "object", "additionalProperties": False, "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
    },
    {
        "name": "ask_staff",
        "description": "Ask the company's staff something only they can decide, such as the price of an item that is not in the product list, or whether to accept a request.",
        "strict": True,
        "input_schema": {
            "type": "object", "additionalProperties": False, "required": ["question", "kind"],
            "properties": {
                "question": {"type": "string"},
                "kind": {"type": "string", "enum": ["price", "approval", "other"]},
            },
        },
    },
    {
        "name": "show_bill",
        "description": "Create or update the bill from the current order and send it to the client to check, with its version code. Only works when every item has a quantity and a price, and the buyer and category are known.",
        "strict": True,
        "input_schema": {"type": "object", "additionalProperties": False, "required": [], "properties": {}},
    },
    {
        "name": "record_confirmation",
        "description": "Record that the client confirmed the latest bill version they were shown.",
        "strict": True,
        "input_schema": {
            "type": "object", "additionalProperties": False, "required": ["version", "client_words"],
            "properties": {
                "version": {"type": "integer", "description": "The bill version number the client agreed to"},
                "client_words": {"type": "string", "description": "The client's exact confirming message"},
            },
        },
    },
    {
        "name": "send_documents",
        "description": "Send the confirmed bill's documents to the client.",
        "strict": True,
        "input_schema": {
            "type": "object", "additionalProperties": False, "required": ["documents"],
            "properties": {"documents": {"type": "array", "items": {"type": "string", "enum": ["invoice", "challan", "quotation"]}}},
        },
    },
    {
        "name": "hand_over",
        "description": "Stop replying in this conversation and alert staff.",
        "strict": True,
        "input_schema": {
            "type": "object", "additionalProperties": False, "required": ["reason"],
            "properties": {"reason": {"type": "string"}},
        },
    },
]


# ── Autopilot: what may go out without a person approving it ───────────────
def _trusted(order):
    from . import app_settings
    needed = app_settings.trusted_after()
    done = ClientOrder.objects.filter(key=order.key, status=ClientOrder.DELIVERED).count()
    return done >= needed


def hold_reason(order, kind):
    """Why a message must wait for approval, or '' if it may go out now."""
    from . import app_settings
    if not app_settings.autopilot_on():
        return 'Autopilot is off: every message waits for your approval.'
    quiet = app_settings.quiet_hours()
    if quiet:
        start, end = quiet
        hour = timezone.localtime().hour
        if (start > end and (hour >= start or hour < end)) or (start < end and start <= hour < end):
            return 'Quiet hours: approve to send now, or it can wait until morning.'
    if not _trusted(order):
        return 'New client: their first orders wait for your approval.'
    if kind == 'bill' and order.transaction:
        limit = app_settings.max_total()
        if float(order.transaction.grand_total) > limit:
            return 'Bill total is above your autopilot limit of Tk %s.' % '{:,.0f}'.format(limit)
    return ''


# ── Tools ──────────────────────────────────────────────────────────────────
def _state(order):
    items = []
    for i, it in enumerate(order.items, start=1):
        items.append({'number': i, 'description': it['description'], 'quantity': it.get('quantity'),
                      'unit': it.get('unit'), 'price': it.get('price'),
                      'price_is_final': bool(it.get('price_is_final')), 'price_from': it.get('price_from') or None,
                      'saved_product': it.get('product')})
    return {
        'items': items,
        'buyer': (order.party.name if order.party else order.buyer_name) or None,
        'buyer_address': order.buyer_address or None,
        'buyer_bin': order.buyer_bin or None,
        'category': order.category.name if order.category else None,
        'prices_include_vat': order.prices_include_vat,
        'bill_number': order.transaction.invoice_number if order.transaction else None,
        'bill_version_shown': order.bill_version or None,
        'status': order.get_status_display(),
        'missing': _missing(order),
    }


def _missing(order):
    missing = []
    for i, it in enumerate(order.items, start=1):
        if it.get('quantity') is None:
            missing.append('quantity of item %d' % i)
        if it.get('price') is None:
            missing.append('price of item %d' % i)
    if not order.items:
        missing.append('items')
    if not order.party_id and not order.buyer_name:
        missing.append('buyer name')
    if not order.category_id:
        missing.append('category')
    return missing


def _tool_update_items(order, args):
    warnings = []
    items = list(order.items)
    to_remove = set()
    for change in args.get('changes') or []:
        action, number = change.get('action'), change.get('item_number')
        target = items[number - 1] if isinstance(number, int) and 1 <= number <= len(items) else None
        if action == 'remove':
            if target is None:
                warnings.append('Item %s does not exist.' % number)
            else:
                to_remove.add(number - 1)
            continue
        if action == 'update' and target is None:
            warnings.append('Item %s does not exist; nothing updated.' % number)
            continue
        if action == 'add':
            description = (change.get('description') or '').strip()
            if not description:
                warnings.append('An item without a name was skipped.')
                continue
            resolved = order_engine._resolve(order, [{
                'description': description, 'quantity': change.get('quantity'),
                'unit': change.get('unit'), 'unit_price': change.get('price')}])[0]
            target = dict(resolved, unit=resolved.get('unit') or change.get('unit') or 'Pcs')
            items.append(target)
        else:
            for field in ('description', 'quantity', 'unit'):
                if change.get(field) is not None:
                    target[field] = change[field]
        if change.get('price') is not None:
            target['price'], target['price_from'] = float(change['price']), 'message'
        if change.get('price_is_final') is not None:
            target['price_is_final'] = bool(change['price_is_final'])
        target['source'] = change.get('source') or target.get('source', '')
        saved = Product.objects.filter(name__iexact=target.get('product') or '').first() if target.get('product') else None
        if saved and target.get('price') and target.get('price_from') == 'message' and saved.upscale_value:
            usual = float(saved.upscale_value)
            if usual and abs(float(target['price']) - usual) / usual > 0.3:
                warnings.append('%s: client price %s is far from the saved price %s. Check with the client or staff.'
                                % (target['description'], target['price'], usual))
    order.items = [it for i, it in enumerate(items) if i not in to_remove]
    order_engine._guess_category(order)
    order.save()
    ledger.record(order, 'change', 'assistant', 'Items updated: %s' % '; '.join(
        '%s %s' % (c.get('action'), c.get('description') or ('item %s' % c.get('item_number'))) for c in args.get('changes') or []),
        {'changes': args.get('changes'), 'items_after': order.items})
    return {'order': _state(order), 'warnings': warnings}


def _tool_set_bill_details(order, args):
    from .inbox_views import match_party
    changed = {}
    if args.get('buyer_name'):
        party = match_party(args['buyer_name'])
        if party and party.party_type == 'BUYER':
            order.party = party
        else:
            order.buyer_name = args['buyer_name'][:255]
        changed['buyer'] = args['buyer_name']
    for field in ('buyer_address', 'buyer_bin'):
        if args.get(field):
            setattr(order, field, args[field])
            changed[field] = args[field]
    if args.get('category'):
        cat = TransactionCategory.objects.filter(name__iexact=args['category']).first()
        if cat is None:
            return {'error': 'Unknown category. Use one of: %s' % ', '.join(
                TransactionCategory.objects.values_list('name', flat=True))}
        order.category = cat
        changed['category'] = cat.name
    if args.get('prices_include_vat') is not None:
        order.prices_include_vat = bool(args['prices_include_vat'])
        changed['prices_include_vat'] = order.prices_include_vat
    order.save()
    if changed:
        ledger.record(order, 'change', 'assistant', 'Bill details set: %s' % ', '.join(
            '%s = %s' % kv for kv in changed.items()), changed)
    return {'order': _state(order)}


def _tool_message_client(order, args):
    text = (args.get('text') or '').strip()
    if not text:
        return {'error': 'Empty message.'}
    msg = whatsapp.queue(order, text, 'assistant', hold_reason(order, 'message'))
    return {'status': 'sent' if msg.status == OutgoingMessage.SENT else 'waiting for staff approval'}


def _tool_ask_staff(order, args):
    question = (args.get('question') or '').strip()
    entry = ledger.record(order, 'owner_question', 'assistant', question, {'kind': args.get('kind')})
    whatsapp.queue(order, 'Order #%d (%s): %s\nReply here, starting with #%d.' % (order.pk, order.title, question, order.pk),
                   'assistant', '' if whatsapp.is_configured() and whatsapp.owner_number() else
                   'Answer this in the app.', to_staff=True)
    return {'status': 'asked staff', 'question_id': entry.seq}


def sync_bill(order):
    """Create the bill from the order, or bring the draft in line with it.

    Items marked price_is_final are converted so the client pays exactly the
    price they stated, VAT and tax included.
    """
    from decimal import Decimal
    if order.transaction and order.transaction.status != 'DRAFT':
        return order.transaction
    order_engine._save_bill(order)
    bill = order.transaction
    bill.buyer_bin = order.buyer_bin or bill.buyer_bin
    if order.buyer_address and not order.party_id:
        bill.buyer_address = order.buyer_address
    bill.save()
    final_any = False
    for item, spec in zip(bill.items.all().order_by('sort_order', 'id'), order.items):
        final = spec.get('price_is_final') or (order.prices_include_vat and spec.get('price_from') == 'message')
        if not final or not item.quantity:
            continue
        final_any = True
        wanted = Decimal(str(spec['price'])) * item.quantity
        paid = item.total_with_tax
        if paid:
            item.base_price = (item.base_price * wanted / paid).quantize(Decimal('0.001'))
            item.save()
    if final_any:
        bill.calculation_mode = 'reverse'
        bill.save()
    return bill


def _tool_show_bill(order, args):
    from .app_views import client_whatsapp_text
    missing = _missing(order)
    if missing:
        return {'error': 'Cannot show the bill yet. Missing: %s.' % ', '.join(missing)}
    bill = sync_bill(order)
    if bill.status != 'DRAFT':
        return {'error': 'The bill was already approved; staff must make any change.'}
    order.status = ClientOrder.SENT
    order.sent_at = timezone.now()
    order.save()
    code = ledger.new_bill_version(order, 'assistant')
    text = (client_whatsapp_text(bill) + '\n\nBill version %d · code %s\nReply *OK* to confirm, or tell us what to change.'
            % (order.bill_version, code))
    msg = whatsapp.queue(order, text, 'assistant', hold_reason(order, 'bill'))
    return {'status': 'sent' if msg.status == OutgoingMessage.SENT else 'waiting for staff approval',
            'version': order.bill_version, 'code': code, 'total': str(round(bill.grand_total, 2))}


def _tool_record_confirmation(order, args):
    shown = ledger.last_shown(order)
    if shown is None:
        return {'error': 'No bill has been shown to the client yet.'}
    if int(args.get('version') or 0) != order.bill_version:
        return {'error': 'The client was last shown version %d. A confirmation must be for that version.'
                % order.bill_version}
    sent = OutgoingMessage.objects.filter(order=order, status=OutgoingMessage.SENT,
                                          text__contains='code %s' % order.version_code).order_by('sent_at').first()
    if sent is None:
        return {'error': 'That bill version has not been sent to the client yet (it may be waiting for approval).'}
    reply = order.messages.filter(received_at__gt=sent.sent_at).order_by('-received_at').first()
    if reply is None:
        return {'error': 'The client has not replied since the bill was sent.'}
    order_engine.confirm(order, by_client=True)
    ledger.record(order, 'client_confirmed', 'client',
                  'Client confirmed bill version %d (code %s): "%s"' % (order.bill_version, order.version_code, reply.text),
                  {'version': order.bill_version, 'code': order.version_code, 'message': reply.text,
                   'wa_id': reply.wa_id, 'received_at': reply.received_at, 'bill': shown.data.get('bill')})
    return {'status': 'confirmed', 'bill': order.transaction.invoice_number}


def _tool_send_documents(order, args):
    if order.status not in (ClientOrder.CONFIRMED, ClientOrder.DELIVERED):
        return {'error': 'Documents go out only after the client confirms.'}
    from . import pdf_render
    results = {}
    for doc in args.get('documents') or []:
        caption = order_engine.deliver_caption(order, doc)
        try:
            pdf = pdf_render.render(order.transaction, doc)       # the same PDF as "Download PDF"
        except pdf_render.RenderError as exc:
            whatsapp.queue(order, caption, 'assistant', 'Make the PDF from the app: %s' % exc, document=doc)
            results[doc] = 'waiting: PDF to be sent from the app'
            continue
        msg = whatsapp.queue(order, caption, 'assistant', hold_reason(order, 'document'), document=doc, pdf=pdf)
        results[doc] = 'sent' if msg.status == OutgoingMessage.SENT else 'waiting for staff approval'
        if msg.status == OutgoingMessage.SENT and doc == 'invoice':
            order.status = ClientOrder.DELIVERED
            order.delivered = list(set(order.delivered + [doc]))
            order.save()
    return {'documents': results}


def _tool_hand_over(order, args):
    order.paused, order.paused_reason = True, (args.get('reason') or '')[:255]
    order.save(update_fields=['paused', 'paused_reason', 'updated_at'])
    ledger.record(order, 'handoff', 'assistant', 'Handed over to staff: %s' % order.paused_reason)
    whatsapp.queue(order, 'Order #%d (%s) needs you: %s' % (order.pk, order.title, order.paused_reason),
                   'assistant', '' if whatsapp.is_configured() and whatsapp.owner_number() else 'See the app.',
                   to_staff=True)
    return {'status': 'handed over'}


HANDLERS = {
    'update_items': _tool_update_items, 'set_bill_details': _tool_set_bill_details,
    'message_client': _tool_message_client, 'ask_staff': _tool_ask_staff, 'show_bill': _tool_show_bill,
    'record_confirmation': _tool_record_confirmation, 'send_documents': _tool_send_documents,
    'hand_over': _tool_hand_over,
}


# ── One turn of the assistant ──────────────────────────────────────────────
def _transcript(order):
    lines = []
    for e in order.events.order_by('seq'):
        if e.kind in ('message_in', 'message_out', 'bill_shown', 'client_confirmed', 'owner',
                      'owner_question', 'handoff', 'document_sent'):
            lines.append('[%s] %s: %s' % (timezone.localtime(e.at).strftime('%d %b %H:%M'), e.get_kind_display(), e.summary))
    return '\n'.join(lines[-60:])


def _images(order, since_seq):
    blocks = []
    for n, msg in enumerate(order.messages.exclude(media='').order_by('received_at'), start=1):
        if not (msg.media_type or '').startswith('image/'):
            continue
        try:
            with msg.media.open('rb') as fh:
                data = base64.standard_b64encode(fh.read()).decode()
        except OSError:
            continue
        blocks.append({'type': 'text', 'text': 'Photo %d (received %s):' % (n, timezone.localtime(msg.received_at).strftime('%d %b %H:%M'))})
        blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': msg.media_type, 'data': data}})
    return blocks[-2 * MAX_IMAGES:]


def run(order):
    """Let the assistant act on everything new in this order."""
    if order.paused or order.status in (ClientOrder.DELIVERED, ClientOrder.CANCELLED):
        return
    with _lock_for(order.pk):
        order.refresh_from_db()
        context = {
            'order_number': order.pk,
            'client': order.title,
            'client_is_known': bool(order.party_id),
            'previous_completed_orders': ClientOrder.objects.filter(key=order.key, status=ClientOrder.DELIVERED).count(),
            'categories': list(TransactionCategory.objects.values_list('name', flat=True)),
            'order': _state(order),
        }
        content = _images(order, 0) + [{'type': 'text', 'text': (
            'Order record so far:\n%s\n\nCurrent order (from the system):\n%s\n\n'
            'Decide what to do next and use the tools. If nothing needs doing, reply with no tool call.'
            % (_transcript(order), json.dumps(context, ensure_ascii=False, indent=1)))}]
        messages = [{'role': 'user', 'content': content}]
        system = [{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}]
        for _step in range(MAX_STEPS):
            response = ai_client.call('order_assistant', order=order, system=system, tools=TOOLS, messages=messages)
            if response.stop_reason == 'refusal':
                _tool_hand_over(order, {'reason': 'The AI could not handle this conversation.'})
                return
            if response.stop_reason == 'pause_turn':
                messages.append({'role': 'assistant', 'content': response.content})
                continue
            calls = [b for b in response.content if b.type == 'tool_use']
            if not calls:
                return
            messages.append({'role': 'assistant', 'content': response.content})
            results = []
            for call in calls:
                handler = HANDLERS.get(call.name)
                try:
                    outcome = handler(order, call.input) if handler else {'error': 'unknown tool'}
                except Exception as exc:                  # keep going; tell the model what failed
                    logger.exception('order assistant tool %s failed', call.name)
                    outcome = {'error': 'This action failed: %s' % exc}
                order.refresh_from_db()
                results.append({'type': 'tool_result', 'tool_use_id': call.id,
                                'content': json.dumps(outcome, ensure_ascii=False, default=str),
                                'is_error': 'error' in outcome})
            messages.append({'role': 'user', 'content': results})
        _tool_hand_over(order, {'reason': 'The assistant took too many steps on one message.'})


def handle_new_messages():
    """Fold new messages into orders, then let the assistant act on each order."""
    touched = []
    pending = InboxMessage.objects.filter(order__isnull=True, status='NEW',
                                          source__in=order_engine.AUTO_SOURCES).order_by('received_at', 'id')
    for msg in pending:
        order = attach(msg)
        if order is not None and order.pk not in touched:
            touched.append(order.pk)
    for pk in touched:
        try:
            run(ClientOrder.objects.get(pk=pk))
        except ai_client.NotConfigured:
            return
        except Exception:
            logger.exception('order assistant failed for order %s', pk)


def attach(msg):
    """Put a message into its client's open order (starting one for an order or a photo)."""
    from .inbox_views import match_party
    from .text_import import extract_items
    key = (msg.chat or msg.sender or msg.phone or '').strip()
    if not key:
        return None
    order = ClientOrder.objects.filter(key=key, status__in=ClientOrder.OPEN).order_by('-id').first()
    if order is None:
        raw, _ = extract_items(msg.text)
        if not any(not r.get('weak') for r in raw) and not msg.media:
            return None
        party = None if msg.chat else match_party(msg.phone or msg.sender)
        order = ClientOrder.objects.create(key=key, title=key[:120], party=party,
                                           phone=msg.phone or order_engine._phone_for(msg.sender, party))
        ledger.record(order, 'system', 'system', 'Order started from a WhatsApp message from %s.' % key,
                      {'phone': order.phone, 'known_client': bool(party)})
    msg.order = order
    msg.save(update_fields=['order'])
    ledger.record(order, 'message_in', 'client', msg.text + (' [with %s]' % msg.media_type if msg.media else ''),
                  {'message': msg.pk, 'wa_id': msg.wa_id, 'phone': msg.phone, 'received_at': msg.received_at,
                   'media': msg.media.name if msg.media else '', 'sender': msg.sender})
    return order
