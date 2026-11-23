"""The permanent, tamper-evident record of every client order.

``record()`` appends an entry to the order's log. Each entry carries the
SHA-256 hash of its own contents plus the hash of the entry before it, so the
entries form a chain: changing, inserting or removing any entry - even
directly in the database - breaks every hash after it, and ``verify()``
reports exactly where.

Bill versions get a short code (``version_code``) taken from their hash. The
code is written into the message the client receives, so the client's own
WhatsApp chat holds a copy of what they were shown and agreed to.
"""
import hashlib
import json
import threading
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import ClientOrder, OrderEvent

GENESIS = '0' * 64
_lock = threading.Lock()


def _canonical(value):
    """JSON-safe, deterministic form of an entry's data."""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


def _digest(order_id, seq, at, kind, actor, user_id, summary, data, prev_hash):
    body = json.dumps({
        'order': order_id, 'seq': seq, 'at': at.isoformat(), 'kind': kind, 'actor': actor,
        'user': user_id, 'summary': summary, 'data': data, 'prev': prev_hash,
    }, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def record(order, kind, actor, summary, data=None, user=None):
    """Append one entry to ``order``'s record and return it."""
    data = _canonical(data or {})
    user_id = user.pk if (user is not None and getattr(user, 'is_authenticated', False)) else None
    with _lock, transaction.atomic():
        last = OrderEvent.objects.filter(order=order).order_by('-seq').first()
        seq = (last.seq + 1) if last else 1
        prev = last.hash if last else GENESIS
        at = timezone.now().replace(microsecond=0)
        entry = OrderEvent(order=order, seq=seq, at=at, kind=kind, actor=actor, user_id=user_id,
                           summary=summary, data=data, prev_hash=prev,
                           hash=_digest(order.pk, seq, at, kind, actor, user_id, summary, data, prev))
        entry.save()
    return entry


def verify(order):
    """Check the whole chain. Returns (ok, message)."""
    prev = GENESIS
    expected_seq = 1
    for entry in OrderEvent.objects.filter(order=order).order_by('seq'):
        if entry.seq != expected_seq:
            return False, 'Entry %d is missing.' % expected_seq
        if entry.prev_hash != prev:
            return False, 'Entry %d does not follow entry %d.' % (entry.seq, entry.seq - 1)
        if _digest(order.pk, entry.seq, entry.at, entry.kind, entry.actor, entry.user_id, entry.summary,
                   _canonical(entry.data), entry.prev_hash) != entry.hash:
            return False, 'Entry %d has been changed after it was written.' % entry.seq
        prev = entry.hash
        expected_seq += 1
    count = expected_seq - 1
    return True, 'All %d entries are intact.' % count


# ── Bill versions ──────────────────────────────────────────────────────────
def bill_snapshot(order):
    """Exactly what the client is shown: lines, totals and the bill number."""
    bill = order.transaction
    lines = []
    for item in bill.items.all().order_by('sort_order', 'id'):
        lines.append({
            'description': item.display_description,
            'quantity': str(item.quantity),
            'unit': item.unit,
            'unit_price': str(round(item.billed_unit_price, 2)),
            'amount': str(round(item.billed_total, 2)),
        })
    return {
        'bill': bill.invoice_number,
        'buyer': bill.buyer_name,
        'lines': lines,
        'subtotal': str(round(bill.display_subtotal, 2)),
        'vat': str(round(bill.total_vat, 2)),
        'tax': str(round(bill.total_tax, 2)),
        'duty': str(round(bill.total_duty, 2)),
        'total': str(round(bill.grand_total, 2)),
        'calculation': bill.calculation_mode,
    }


def new_bill_version(order, actor='system', user=None):
    """Record a new version of the bill as shown to the client; returns its code."""
    snapshot = bill_snapshot(order)
    order.bill_version += 1
    code_source = json.dumps({'order': order.pk, 'version': order.bill_version, 'bill': snapshot}, sort_keys=True)
    order.version_code = hashlib.sha256(code_source.encode()).hexdigest()[:8].upper()
    order.save(update_fields=['bill_version', 'version_code', 'updated_at'])
    record(order, 'bill_shown', actor,
           'Bill %s version %d (code %s) shown to the client: total Tk %s.'
           % (snapshot['bill'], order.bill_version, order.version_code, snapshot['total']),
           {'version': order.bill_version, 'code': order.version_code, 'bill': snapshot}, user=user)
    return order.version_code


def last_shown(order):
    return OrderEvent.objects.filter(order=order, kind='bill_shown').order_by('-seq').first()


def ensure_order_ledger(order):
    """Orders made before the record existed start their chain on first use."""
    if not OrderEvent.objects.filter(order=order).exists():
        record(order, 'system', 'system', 'Order record started.', {'status': order.status})


__all__ = ['record', 'verify', 'new_bill_version', 'bill_snapshot', 'last_shown', 'ClientOrder']
