"""Message Inbox: WhatsApp / text messages -> bills.

Messages arrive four ways (see InboxMessage.SOURCE_CHOICES). Three of them
work on the plain website too; the automatic WhatsApp capture needs the
Android app, which drops captured messages into a spool folder that
``ingest_spool`` drains here.
"""
import difflib
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import permission_required
from .models import BusinessParty, InboxMessage, Product, TransactionCategory
from .text_import import extract_items, message_fingerprint, normalize, parse_chat_export

MAX_IMPORT_MESSAGES = 1000          # newest messages kept from one export
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


# ── Storing messages ───────────────────────────────────────────────────────
def _aware(when):
    if when is None:
        return timezone.now()
    if timezone.is_naive(when):
        try:
            return timezone.make_aware(when)
        except Exception:
            return timezone.now()
    return when


def store_message(source, text, sender='', when=None, chat='', app='', revive=False):
    """Save one message unless we already have it. Returns (obj, created)."""
    text = normalize(text).strip()
    if not text:
        return None, False
    sender = (sender or '').strip()[:120]
    chat = (chat or '').strip()[:120]
    external_id = message_fingerprint(sender, text, when, salt=chat)
    obj, created = InboxMessage.objects.get_or_create(
        external_id=external_id,
        defaults={
            'source': source, 'app': (app or '')[:40], 'sender': sender, 'chat': chat,
            'text': text, 'received_at': _aware(when),
        },
    )
    if not created and revive and obj.status != 'NEW':
        # The user deliberately pasted the same text again: put it back in New.
        obj.status, obj.transaction, obj.received_at = 'NEW', None, timezone.now()
        obj.save(update_fields=['status', 'transaction', 'received_at'])
        created = True
    return obj, created


def store_text_block(source, text, default_sender='', app='', revive=False):
    """Store a block of text: a chat export becomes many messages, anything
    else becomes one. Returns the number of new messages."""
    parsed = parse_chat_export(text)
    if parsed:
        added = 0
        for msg in parsed[-MAX_IMPORT_MESSAGES:]:
            _, created = store_message(source, msg['text'], msg['sender'], msg['when'], app=app)
            added += int(created)
        return added
    _, created = store_message(source, text, default_sender, app=app, revive=revive)
    return int(created)


def _spool_dir():
    return getattr(settings, 'INBOX_SPOOL_DIR', None)


def ingest_spool():
    """Drain messages the Android app captured while we weren't looking.

    The app writes one small JSON file per batch (written as .tmp, then
    renamed, so a file is always complete by the time we see it).
    """
    spool = _spool_dir()
    if not spool or not os.path.isdir(spool):
        return 0
    added = 0
    for name in sorted(os.listdir(spool)):
        if not name.endswith('.json'):
            continue
        path = os.path.join(spool, name)
        try:
            with open(path, encoding='utf-8') as fh:
                batch = json.load(fh)
        except (OSError, ValueError):
            batch = []
        if isinstance(batch, dict):
            batch = [batch]
        for entry in batch if isinstance(batch, list) else []:
            if not isinstance(entry, dict):
                continue
            text = entry.get('text') or ''
            source = entry.get('source') if entry.get('source') in ('notification', 'share') else 'notification'
            when = None
            try:
                if entry.get('ts'):
                    when = datetime.fromtimestamp(int(entry['ts']) / 1000.0, tz=dt_timezone.utc)
            except (TypeError, ValueError, OverflowError, OSError):
                when = None
            if source == 'share':
                added += store_text_block('share', text, entry.get('sender') or '', app=entry.get('app') or '', revive=True)
            else:
                _, created = store_message(source, text, entry.get('sender') or '', when,
                                           chat=entry.get('chat') or '', app=entry.get('app') or '')
                added += int(created)
        try:
            os.remove(path)
        except OSError:
            pass
    return added


def _read_upload(upload):
    """Text of an uploaded .txt, or of the chat .txt inside an exported .zip."""
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('"%s" is larger than 8 MB.' % upload.name)
    if data[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith('.txt')]
                if not names:
                    raise ValueError('"%s" has no chat .txt file inside.' % upload.name)
                info = zf.getinfo(names[0])
                if info.file_size > MAX_UPLOAD_BYTES:
                    raise ValueError('The chat inside "%s" is larger than 8 MB.' % upload.name)
                data = zf.read(names[0])
        except zipfile.BadZipFile:
            raise ValueError('"%s" is not a valid zip file.' % upload.name)
    for encoding in ('utf-8-sig', 'utf-16'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', 'replace')


# ── Matching a sender to a saved contact ───────────────────────────────────
def _digits(value):
    return re.sub(r'\D', '', value or '')


def match_party(sender):
    """Find the saved contact a message came from (by number, then by name)."""
    sender = (sender or '').strip()
    if not sender:
        return None
    parties = list(BusinessParty.objects.filter(is_deleted=False))
    # BUYER first: someone messaging an order is normally the customer.
    parties.sort(key=lambda p: 0 if p.party_type == 'BUYER' else 1)
    digits = _digits(sender)
    if len(digits) >= 8:
        tail = digits[-10:]
        for p in parties:
            for stored in (_digits(p.whatsapp_number), _digits(p.contact)):
                if len(stored) >= 8 and stored[-10:] == tail:
                    return p
    name = sender.lower()
    for p in parties:
        if p.name.strip().lower() == name:
            return p
    if len(name) >= 4:
        for p in parties:
            pn = p.name.strip().lower()
            if pn and (name in pn or pn in name):
                return p
    return None


def inbox_context_for_bill(id_string):
    """Context the bill form needs when opened from the inbox."""
    ids = [int(x) for x in re.findall(r'\d+', id_string or '')][:200]
    if not ids:
        return {}
    msgs = list(InboxMessage.objects.filter(pk__in=ids).order_by('received_at', 'id'))
    if not msgs:
        return {}
    senders = []
    for m in msgs:
        if m.sender and m.sender not in senders:
            senders.append(m.sender)
    party = None
    for s in senders:
        party = match_party(s)
        if party:
            break
    return {
        'inbox_ids': ','.join(str(m.pk) for m in msgs),
        'inbox_text': '\n'.join(m.text for m in msgs),
        'inbox_senders': ', '.join(senders),
        'inbox_count': len(msgs),
        'inbox_party': party,
    }


def mark_messages_billed(id_string, transaction):
    ids = [int(x) for x in re.findall(r'\d+', id_string or '')][:200]
    if not ids:
        return 0
    return InboxMessage.objects.filter(pk__in=ids).update(status='BILLED', transaction=transaction)


# ── Pages ──────────────────────────────────────────────────────────────────
@login_required
@permission_required('p_create_bill')
def inbox_list(request):
    ingest_spool()
    status = request.GET.get('status', 'NEW').upper()
    if status not in ('NEW', 'BILLED', 'ARCHIVED'):
        status = 'NEW'
    query = (request.GET.get('q') or '').strip()
    qs = InboxMessage.objects.filter(status=status).select_related('transaction')
    if query:
        qs = qs.filter(Q(text__icontains=query) | Q(sender__icontains=query) | Q(chat__icontains=query))
    msgs = list(qs[:400])

    # One card per conversation, newest conversation first, messages oldest first.
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

    counts = dict(InboxMessage.objects.values_list('status').annotate(n=Count('id')))
    return render(request, 'documents/inbox_list.html', {
        'groups': groups,
        'status': status,
        'query': query,
        'counts': {'NEW': counts.get('NEW', 0), 'BILLED': counts.get('BILLED', 0), 'ARCHIVED': counts.get('ARCHIVED', 0)},
        'shown': len(msgs),
    })


@login_required
@permission_required('p_create_bill')
@require_POST
def inbox_import(request):
    """Paste text and/or upload WhatsApp chat exports (.txt / .zip)."""
    added, problems = 0, []
    text = request.POST.get('text') or ''
    sender = (request.POST.get('sender') or '').strip()
    if text.strip():
        added += store_text_block('paste', text, sender, revive=True)
    for upload in request.FILES.getlist('files'):
        try:
            added += store_text_block('upload', _read_upload(upload), sender, app='WhatsApp')
        except ValueError as exc:
            problems.append(str(exc))
    for p in problems:
        messages.error(request, p)
    if added:
        messages.success(request, '%d new message(s) added to the inbox.' % added)
    elif not problems:
        messages.info(request, 'Nothing new to add - those messages are already in the inbox.')
    return redirect('inbox_list')


@login_required
@permission_required('p_create_bill')
@require_POST
def inbox_action(request):
    ids = [int(x) for x in request.POST.getlist('ids') if str(x).isdigit()]
    action = request.POST.get('action')
    back = redirect(request.POST.get('next') or 'inbox_list')
    if not ids:
        messages.error(request, 'Select at least one message first.')
        return back
    qs = InboxMessage.objects.filter(pk__in=ids)
    if action == 'bill':
        return redirect('/bills/new/?inbox=' + ','.join(str(i) for i in ids))
    if action == 'archive':
        messages.success(request, '%d message(s) archived.' % qs.update(status='ARCHIVED'))
    elif action == 'restore':
        messages.success(request, '%d message(s) moved back to New.' % qs.update(status='NEW', transaction=None))
    elif action == 'delete':
        count = qs.count()
        qs.delete()
        messages.success(request, '%d message(s) deleted.' % count)
    return back


@login_required
def inbox_status_api(request):
    """Cheap poll for the sidebar badge; also drains the capture spool."""
    added = ingest_spool()
    return JsonResponse({'new': InboxMessage.objects.filter(status='NEW').count(), 'added': added})


# ── Offline free-form parser for the bill form ─────────────────────────────
def _resolve_category_strict(line):
    """Match a line to a category only when it is clearly the category name."""
    text = (line or '').strip().lower()
    if len(text) < 3:
        return None
    cats = list(TransactionCategory.objects.all())
    for c in cats:
        if c.name.strip().lower() == text:
            return c
    for c in cats:
        name = c.name.strip().lower()
        if len(name) >= 4 and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text):
            return c
    close = difflib.get_close_matches(text, [c.name.strip().lower() for c in cats], n=1, cutoff=0.85)
    if close:
        return next((c for c in cats if c.name.strip().lower() == close[0]), None)
    return None


@login_required
@require_POST
def quick_bill_smart_parse(request):
    """Read messy text (a WhatsApp message, an SMS, a note) with no internet.

    Understands "5 kg rice @ 60", "Cable clip - 12 pcs", "Oil 2 ltr = 360",
    numbered/bulleted lists, Bengali digits and the strict "name | qty | price"
    format, then resolves every row through the same product matching as the
    other Quick Bill parsers.
    """
    from .views import _quick_build_rows

    try:
        payload = json.loads(request.body or '{}')
    except (ValueError, TypeError):
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    text = (payload.get('text') or '').strip()
    if not text:
        return JsonResponse({'error': 'no text supplied'}, status=400)

    items, skipped = extract_items(text)

    category, category_line = None, None
    if items and items[0].get('weak'):
        category = _resolve_category_strict(items[0]['description'])
        if category is not None:
            category_line = items[0]['description']
            items = items[1:]
    if category is None and payload.get('category_id'):
        category = TransactionCategory.objects.filter(pk=payload.get('category_id')).first()

    rows = _quick_build_rows(category, items)
    # Lines with no numbers are only kept when they are plausibly an item:
    # they match a saved product, or the whole text is a short plain list.
    has_strong = any(not it.get('weak') for it in items)
    kept = []
    for item, row in zip(items, rows):
        if item.get('weak') and not row['matched_product']:
            if has_strong or len(item['description'].split()) > 5:
                skipped.append(item['description'])
                continue
        kept.append(row)
    for i, row in enumerate(kept, start=1):
        row['idx'] = i

    # No category named in the text and none picked on the form: use the one
    # most of the recognised products belong to (or the only one that exists).
    if category is None and kept:
        names = [row['matched_product'] for row in kept if row['matched_product']]
        tally = {}
        for cat_id in Product.objects.filter(name__in=names).values_list('category_id', flat=True):
            tally[cat_id] = tally.get(cat_id, 0) + 1
        if tally:
            category = TransactionCategory.objects.filter(pk=max(tally, key=tally.get)).first()
        elif TransactionCategory.objects.count() == 1:
            category = TransactionCategory.objects.first()

    if not kept:
        return JsonResponse({'error': 'Could not find any items in that text. '
                                      'Write one item per line, e.g. "Rice 5 kg @ 60".'}, status=422)
    return JsonResponse({
        'ok': True,
        'category': ({'id': category.id, 'name': category.name} if category else None),
        'category_line': category_line,
        'rows': kept,
        'skipped': skipped,
    })
