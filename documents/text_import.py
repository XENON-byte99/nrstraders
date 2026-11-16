"""Turn free-form text (WhatsApp messages, SMS, notes) into bill line items.

Everything here is plain Python with no network access, so it works the same
on the website and inside the offline mobile app.

Two jobs:

* ``parse_chat_export``  - split a WhatsApp "Export chat" .txt (or any pasted
  block of chat lines) into individual messages with sender + time.
* ``extract_items``      - read one or more messages and pull out
  ``{description, quantity, unit, unit_price}`` rows, e.g. from
  ``"5 kg rice @ 60"`` or ``"Cable clip - 12 pcs"``.
"""
import hashlib
import re
from datetime import datetime

# ── Normalisation ──────────────────────────────────────────────────────────
_BN_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')
# Invisible direction marks / odd spaces WhatsApp sprinkles into exports.
_INVISIBLE = re.compile('[\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]')
_ODD_SPACES = re.compile('[\u00a0\u202f\u2007\u2009]')


def normalize(text):
    text = _INVISIBLE.sub('', text or '')
    text = _ODD_SPACES.sub(' ', text)
    return text.translate(_BN_DIGITS).replace('\r\n', '\n').replace('\r', '\n')


# ── WhatsApp chat export ───────────────────────────────────────────────────
# Android:  12/05/24, 10:30 PM - Rahim: 5 kg rice
# iPhone:   [12/05/24, 10:30:15 PM] Rahim: 5 kg rice
_CHAT_LINE = re.compile(
    r'^\[?(?P<date>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{2,4}),?\s+'
    r'(?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[AaPp]\.?\s?[Mm]\.?)?)\]?'
    r'\s*(?:[-\u2013]\s*)?(?P<rest>.*)$'
)
_NOT_A_MESSAGE = re.compile(
    r'(<media omitted>|\bomitted$|this message was deleted|you deleted this message|'
    r'messages and calls are end-to-end encrypted|missed (voice|video) call|^null$|'
    r'<attached:|\(file attached\)$)',
    re.I,
)


def _parse_when(date_s, time_s):
    """Best-effort timestamp. Day-first (Bangladesh/UK) unless impossible."""
    parts = [int(p) for p in re.split(r'[/.\-]', date_s)]
    if len(parts) != 3:
        return None
    a, b, c = parts
    if a > 31:                      # yyyy-mm-dd
        year, month, day = a, b, c
    else:
        day, month, year = a, b, c
        if month > 12 and day <= 12:    # clearly month-first (US export)
            day, month = month, day
    if year < 100:
        year += 2000
    t = re.match(r'(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?\s*([AaPp])?', time_s)
    if not t:
        return None
    hour, minute, second = int(t.group(1)), int(t.group(2)), int(t.group(3) or 0)
    ampm = (t.group(4) or '').lower()
    if ampm == 'p' and hour < 12:
        hour += 12
    elif ampm == 'a' and hour == 12:
        hour = 0
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def parse_chat_export(text):
    """Split an exported chat into messages.

    Returns ``[{'sender', 'text', 'when'}]`` (``when`` is a naive datetime or
    None). Returns ``[]`` when the text doesn't look like a chat export, so
    the caller can treat it as one plain message instead.
    """
    messages, current = [], None
    for line in normalize(text).split('\n'):
        m = _CHAT_LINE.match(line.strip())
        if m:
            rest = m.group('rest')
            sender, sep, body = rest.partition(': ')
            if not sep and rest.endswith(':'):
                sender, sep, body = rest[:-1], ':', ''
            if not sep:
                current = None       # system line ("X joined", encryption notice)
                continue
            current = {
                'sender': sender.strip()[:120],
                'text': body.strip(),
                'when': _parse_when(m.group('date'), m.group('time')),
            }
            messages.append(current)
        elif current is not None:
            current['text'] += '\n' + line.rstrip()
    cleaned = []
    for msg in messages:
        msg['text'] = msg['text'].strip()
        if msg['text'] and not _NOT_A_MESSAGE.search(msg['text']):
            cleaned.append(msg)
    return cleaned


def message_fingerprint(sender, text, when=None, salt=''):
    """Stable id so the same message is never stored twice."""
    stamp = when.strftime('%Y%m%d%H%M') if when else ''
    raw = '|'.join([salt, (sender or '').strip().lower(), stamp, (text or '').strip()])
    return hashlib.sha1(raw.encode('utf-8', 'replace')).hexdigest()


# ── Line item extraction ───────────────────────────────────────────────────
_NUM = r'\d[\d,]*(?:\.\d+)?'

# Units that count as a *quantity* unit. Dimension units (mm, cm, inch) are
# deliberately absent: "1 inch Cable Clip" and "08 mm Nut" are product names.
_UNIT_ALIASES = {
    'Kg': ['kg', 'kgs', 'kilo', 'kilos', 'kilogram', 'kilograms', 'keji', 'kejি', 'কেজি', 'কেজী'],
    'Gm': ['g', 'gm', 'gms', 'gram', 'grams', 'গ্রাম'],
    'Ltr': ['l', 'ltr', 'ltrs', 'lt', 'liter', 'liters', 'litre', 'litres', 'লিটার'],
    'Ml': ['ml'],
    'Pcs': ['pcs', 'pc', 'piece', 'pieces', 'pis', 'nos', 'no', 'টি', 'টা', 'পিস', 'পিছ', 'খানা'],
    'Box': ['box', 'boxes', 'বক্স'],
    'Pkt': ['pkt', 'pkts', 'packet', 'packets', 'pack', 'packs', 'প্যাকেট', 'প্যাক'],
    'Dozen': ['dozen', 'dozens', 'dz', 'doz', 'ডজন'],
    'Hali': ['hali', 'হালি'],
    'Bag': ['bag', 'bags', 'bosta', 'ব্যাগ', 'বস্তা'],
    'Set': ['set', 'sets', 'সেট'],
    'Pair': ['pair', 'pairs', 'জোড়া'],
    'Roll': ['roll', 'rolls', 'রোল'],
    'Coil': ['coil', 'coils'],
    'Ft': ['ft', 'feet', 'foot', 'ফুট'],
    'Meter': ['meter', 'meters', 'metre', 'metres', 'mtr', 'mtrs', 'মিটার'],
    'Yard': ['yard', 'yards', 'yd', 'goj', 'gaz', 'গজ'],
    'Bottle': ['bottle', 'bottles', 'btl', 'বোতল'],
    'Can': ['can', 'cans'],
    'Carton': ['carton', 'cartons', 'ctn', 'কার্টন'],
    'Ton': ['ton', 'tons', 'tonne', 'টন'],
    'Mon': ['mon', 'maund', 'মণ'],
    'Bundle': ['bundle', 'bundles', 'bdl', 'বান্ডিল'],
    'Sheet': ['sheet', 'sheets'],
    'Ream': ['ream', 'reams', 'রিম'],
    'Person': ['person', 'persons', 'pax', 'জন'],
    'Day': ['day', 'days', 'দিন'],
    'Night': ['night', 'nights'],
    'Trip': ['trip', 'trips'],
}
_UNIT_LOOKUP = {alias: canon for canon, aliases in _UNIT_ALIASES.items() for alias in aliases}
_UNIT_ALT = '|'.join(sorted((re.escape(a) for a in _UNIT_LOOKUP), key=len, reverse=True))
# (?![\w]) keeps "5 pcs" but not "5 package"; Bengali suffixes have no \b.
_UNIT_END = r'(?![A-Za-z\u0980-\u09ff])'

_CURRENCY = r'(?:tk\.?|taka|bdt|৳|টাকা|rs\.?)'
_PRICE_PATTERNS = [
    # explicit per-unit: "@ 60", "@60tk", "rate 60", "দর 60"
    (re.compile(r'(?:@|\b(?:rate|price|dam|dor|each|per\s+(?:pc|piece|unit|kg))\b|দাম|দর)\s*[:=\-]?\s*'
                r'(?:' + _CURRENCY + r')?\s*(?P<n>' + _NUM + r')\s*(?:' + _CURRENCY + r'|/[-=])?'
                r'(?:\s*(?:/|per)\s*(?:' + _UNIT_ALT + r')' + _UNIT_END + r')?', re.I), 'unit'),
    # "60/kg", "470 tk/pc"
    (re.compile(r'(?P<n>' + _NUM + r')\s*(?:' + _CURRENCY + r')?\s*(?:/|per)\s*(?:' + _UNIT_ALT + r')' + _UNIT_END, re.I), 'unit'),
    # line total: "= 300", "total 300"
    (re.compile(r'(?:=|\btotal\b|মোট)\s*:?\s*(?:' + _CURRENCY + r')?\s*(?P<n>' + _NUM + r')\s*(?:' + _CURRENCY + r'|/[-=])?', re.I), 'total'),
    # "470 tk", "470/-", "৳470", "tk 470"
    (re.compile(r'(?P<n>' + _NUM + r')\s*(?:' + _CURRENCY + r'(?![A-Za-z])|/[-=])', re.I), 'unit'),
    (re.compile(r'(?:' + _CURRENCY + r')\s*(?P<n>' + _NUM + r')', re.I), 'unit'),
]

_QTY_UNIT = re.compile(r'(?<![\w.])(?P<n>' + _NUM + r')\s*(?P<u>' + _UNIT_ALT + r')' + _UNIT_END, re.I)
_QTY_WORD = re.compile(r'\b(?:qty|quantity|পরিমাণ)\s*[:=\-]?\s*(?P<n>' + _NUM + r')', re.I)
_QTY_TIMES = re.compile(r'(?:(?<=\s)|^)[x×*]\s*(?P<n>' + _NUM + r')(?!\S)|(?<![\w.])(?P<n2>' + _NUM + r')\s*[x×](?=\s|$)', re.I)
_TIMES_PRICE = re.compile(r'(?<![\w.])(?P<q>' + _NUM + r')\s*(?P<u>' + _UNIT_ALT + r')?\s*[x×*]\s*(?P<p>' + _NUM + r')(?!\S)', re.I)
_TRAILING_NUM = re.compile(r'(?:^|[\s:\-\u2013=])(?P<n>' + _NUM + r')\s*$')
_LEADING_NUM = re.compile(r'^(?P<n>[1-9][\d,]*(?:\.\d+)?)\s+(?=\D)')
_DIMENSION_AFTER = re.compile(r'^\s*(?:mm|cm|inch|inches|in\b|"|\'|sqft|sq|core|amp|watt|w\b|v\b|volt|gauge|swg|no\b|mg\b|gsm|hp\b|kva|kv\b|way|gang|pin|x\s*\d)', re.I)

_BULLET = re.compile(r'^\s*(?:[-*\u2022\u00b7\u25aa\u25cf\u27a4>#]+\s*|\(?\d{1,3}[.)]\s+|\(\d{1,3}\)\s*)')
_ITEM_SPLIT = re.compile(r',(?!\d{3}(?!\d))\s*|\s+(?:and|ar|এবং|আর|ও)\s+(?=\S*\d)|;')
_STRICT_SPLIT = re.compile(r'\s*[|\t]\s*')
_EDGE_JUNK = re.compile(r'^[\s\-\u2013:=,.@x×*/()]+|[\s\-\u2013:=,@x×*/(]+$')

_NOISE = re.compile(
    r'^(?:hi+|hello|hey|salam|assalamu?\s*alaikum|as-?salamu.*|walaikum.*|slm|bhai|vai|bhaiya|sir|boss|'
    r'ok(?:ay)?|okk+|thanks?|thank\s*you|thx|dhonnobad|ধন্যবাদ|জি|ji|yes|no|hmm+|good\s*(?:morning|evening|night)|'
    r'please|pls|plz|ভাই|স্যার|আসসালামু.*|order|list|items?|অর্ডার)[\s!.,:;\-]*$',
    re.I,
)


def _num(s):
    try:
        return float(str(s).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _cut(line, match):
    return (line[:match.start()] + ' ' + line[match.end():])


def _clean_description(text):
    text = re.sub(r'\s+', ' ', text or '')
    prev = None
    while prev != text:
        prev = text
        text = _EDGE_JUNK.sub('', text)
    text = text.strip()
    if text.endswith(')') and '(' not in text:
        text = text[:-1].strip()
    return text


def _parse_strict(line):
    """The original Quick Bill format: name | qty | price."""
    parts = [p.strip() for p in _STRICT_SPLIT.split(line)]
    return {
        'description': parts[0] if parts else '',
        'quantity': parts[1] if len(parts) > 1 and parts[1] != '' else None,
        'unit': None,
        'unit_price': parts[2] if len(parts) > 2 and parts[2] != '' else None,
        'weak': False,
    }


def _parse_free(line):
    """One free-form fragment -> raw item, or None if nothing usable."""
    work = _BULLET.sub('', line).strip()
    if not work:
        return None
    had_digit = bool(re.search(r'\d', work))
    qty = unit = price = total = None

    # "5 pcs x 470" / "5 x 470"  -> quantity x unit price
    m = _TIMES_PRICE.search(work)
    if m:
        qty, price = _num(m.group('q')), _num(m.group('p'))
        if m.group('u'):
            unit = _UNIT_LOOKUP.get(m.group('u').lower())
        work = _cut(work, m)

    if price is None:
        for pattern, kind in _PRICE_PATTERNS:
            m = pattern.search(work)
            if not m:
                continue
            value = _num(m.group('n'))
            if kind == 'total':
                total = value
            else:
                price = value
            work = _cut(work, m)
            if kind == 'unit':
                break
        # a line may carry both "@60" and "= 300"; drop the leftover total too
        if price is not None:
            m = _PRICE_PATTERNS[2][0].search(work)
            if m:
                work = _cut(work, m)

    if qty is None:
        m = _QTY_WORD.search(work)
        if m:
            qty = _num(m.group('n'))
            work = _cut(work, m)
    if qty is None:
        m = _QTY_UNIT.search(work)
        if m:
            qty, unit = _num(m.group('n')), _UNIT_LOOKUP.get(m.group('u').lower())
            work = _cut(work, m)
    if qty is None:
        m = _QTY_TIMES.search(work)
        if m:
            qty = _num(m.group('n') or m.group('n2'))
            work = _cut(work, m)
    if qty is None:
        m = _TRAILING_NUM.search(work)
        if m:
            qty = _num(m.group('n'))
            work = work[:m.start()] + ' '
    if qty is None:
        m = _LEADING_NUM.match(work)
        if m and not _DIMENSION_AFTER.match(work[m.end():]):
            qty = _num(m.group('n'))
            work = work[m.end():]
    # a bare unit word left behind by "rice kg 5" style lines
    if unit is None and qty is not None:
        m = re.search(r'(?<![A-Za-z\u0980-\u09ff])(' + _UNIT_ALT + r')' + _UNIT_END, work, re.I)
        if m and len(m.group(1)) > 1 and m.end() >= len(work.rstrip()) - 0:
            unit = _UNIT_LOOKUP.get(m.group(1).lower())
            work = _cut(work, m)

    description = _clean_description(work)
    if not description or not re.search(r'[^\W\d_]', description):
        return None
    if price is None and total is not None:
        price = round(total / qty, 3) if qty else total
    return {
        'description': description[:255],
        'quantity': qty,
        'unit': unit,
        'unit_price': price,
        'weak': not had_digit,
    }


def extract_items(text):
    """Pull bill rows out of free-form text.

    Returns ``(items, skipped)``. Each item is
    ``{'description', 'quantity', 'unit', 'unit_price', 'weak'}`` where
    ``weak`` marks a line with no numbers at all (it might be an item name,
    or just chatter - the caller decides using the product list).
    ``skipped`` lists the lines that were ignored.
    """
    text = normalize(text)
    chat = parse_chat_export(text)
    if chat:
        text = '\n'.join(m['text'] for m in chat)

    items, skipped = [], []
    for raw_line in text.split('\n'):
        line = raw_line.strip()
        if not line:
            continue
        if '|' in line or '\t' in line:
            item = _parse_strict(_BULLET.sub('', line))
            if item['description']:
                items.append(item)
            continue
        if _NOISE.match(_BULLET.sub('', line)):
            skipped.append(line)
            continue
        fragments = [f for f in _ITEM_SPLIT.split(line) if f and f.strip()]
        # Only treat commas as item separators when the pieces look like items.
        if len(fragments) > 1 and sum(1 for f in fragments if re.search(r'\d', f)) < 2:
            fragments = [line]
        found = False
        for fragment in fragments:
            item = _parse_free(fragment)
            if item:
                items.append(item)
                found = True
        if not found:
            skipped.append(line)
    return items, skipped
