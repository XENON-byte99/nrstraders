"""Two-way sync between the phone app and the live website.

The phone keeps a full copy of the data and works offline. Whenever it is
online it exchanges changes with the live server over HTTPS:

* the phone sends the records it changed (and what it deleted);
* the server applies them, then sends back everything that changed there.

Records are matched by their ``uuid``. The server numbers every change
(``sync_rev``), so "what changed since last time" never depends on clocks.
If the same record was changed on both sides, the server's version wins and
the phone is told, so nothing is silently overwritten on the live site.

This module is shared: the server half (the ``api_*`` views) runs on the
website, the phone half lives in ``sync_client.py``. Both use the same
``serialize`` / ``apply_record`` code so the two sides cannot drift apart.
"""
import datetime
import decimal
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid as uuidlib

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, connection, models, transaction
from django.db.models.signals import post_delete
from django.http import FileResponse, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import (BusinessParty, CheckAuthorization, Product, SyncableModel, SyncCounter, SyncDevice,
                     SyncTombstone, Transaction, TransactionCategory, TransactionItem, VisualRequisition)

PROTOCOL = 1
# Parents before children, so foreign keys can be resolved as records arrive.
SYNC_MODELS = [BusinessParty, TransactionCategory, Product, Transaction, TransactionItem,
               VisualRequisition, CheckAuthorization]
MODEL_BY_NAME = {m.__name__: m for m in SYNC_MODELS}
# Records that are "the same thing" when their names match, even if they were
# created separately on the phone and on the website.
NATURAL_KEYS = {'TransactionCategory': ('name',), 'Product': ('name', 'category'),
                'CheckAuthorization': ('reference_number',)}
SKIP_FIELDS = {'id', 'uuid', 'is_dirty', 'sync_rev'}
USER_FIELDS_SKIP = {'id', 'last_login'}
BACKUP_KEEP = 20
BACKUP_EVERY_SECONDS = 24 * 3600

_local = threading.local()


def is_device():
    return getattr(settings, 'SYNC_ROLE', 'server') == 'device'


# ── Deletes ────────────────────────────────────────────────────────────────
class applying_remote:
    """While applying changes that came from the other side, do not record
    the resulting deletes as new local changes to send back."""

    def __enter__(self):
        _local.depth = getattr(_local, 'depth', 0) + 1

    def __exit__(self, *exc):
        _local.depth -= 1


def _remember_delete(sender, instance, **kwargs):
    if not issubclass(sender, SyncableModel) or sender not in SYNC_MODELS or instance.uuid is None:
        return
    if getattr(_local, 'depth', 0) and is_device():
        return
    SyncTombstone.objects.create(
        model=sender.__name__, uuid=instance.uuid,
        sync_rev=0 if is_device() else SyncCounter.next(),
        is_dirty=is_device(),
    )


post_delete.connect(_remember_delete, dispatch_uid='nrs-sync-tombstone')


# ── Turning records into plain data and back ───────────────────────────────
def _plain(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuidlib.UUID)):
        return str(value)
    return value


def serialize(obj):
    User = get_user_model()
    fields = {}
    for field in obj._meta.concrete_fields:
        if field.name in SKIP_FIELDS:
            continue
        if isinstance(field, models.ForeignKey):
            target = getattr(obj, field.name)
            if target is None:
                fields[field.name] = None
            elif field.related_model is User:
                fields[field.name] = {'user': target.username}
            else:
                fields[field.name] = {'uuid': str(target.uuid)}
        elif isinstance(field, models.FileField):
            fields[field.name] = getattr(obj, field.name).name or ''
        else:
            fields[field.name] = _plain(field.value_from_object(obj))
    for field in obj._meta.many_to_many:
        if field.related_model is User and obj.pk:
            fields[field.name] = sorted(getattr(obj, field.name).values_list('username', flat=True))
    return {'model': type(obj).__name__, 'uuid': str(obj.uuid), 'rev': obj.sync_rev, 'fields': fields}


class Rejected(Exception):
    """This record cannot be applied; the reason is shown to the user."""


def _assign(obj, data):
    """Copy serialized fields onto ``obj``. Returns pending many-to-many values."""
    User = get_user_model()
    m2m = {}
    for field in obj._meta.concrete_fields:
        if field.name in SKIP_FIELDS or field.name not in data:
            continue
        value = data[field.name]
        if isinstance(field, models.ForeignKey):
            target = None
            if value:
                if field.related_model is User:
                    target = User.objects.filter(username=value.get('user')).first()
                else:
                    target = field.related_model.objects.filter(uuid=value.get('uuid')).first()
            if target is None and not field.null:
                raise Rejected('it refers to a %s that does not exist here' % field.related_model._meta.verbose_name)
            setattr(obj, field.name, target)
        elif isinstance(field, models.FileField):
            setattr(obj, field.name, value or '')
        else:
            setattr(obj, field.attname, field.to_python(value))
    for field in obj._meta.many_to_many:
        if field.name in data and field.related_model is User:
            m2m[field.name] = list(User.objects.filter(username__in=data[field.name]))
    return m2m


def _save(obj, m2m, rev=None):
    """Save a record that arrived through sync.

    On the server it takes the next change number (so other phones receive
    it). On a phone it keeps the server's number and stays "clean".
    """
    stamp = obj.updated_at
    if is_device():
        if rev is not None:
            obj.sync_rev = rev
        obj.is_dirty = False
        obj.save(skip_dirty=True, keep_rev=True)
    else:
        obj.save(skip_dirty=True)
    if stamp is not None:          # keep the original edit time, not "when it synced"
        type(obj).objects.filter(pk=obj.pk).update(updated_at=stamp)
    for name, values in m2m.items():
        getattr(obj, name).set(values)


def _natural_match(model, data):
    """An existing record that is the same thing as an incoming new one."""
    key = NATURAL_KEYS.get(model.__name__)
    if not key:
        return None
    lookup = {}
    for name in key:
        value = data.get(name)
        field = model._meta.get_field(name)
        if isinstance(field, models.ForeignKey):
            target = field.related_model.objects.filter(uuid=(value or {}).get('uuid')).first() if value else None
            if target is None:
                return None
            lookup[name] = target
        else:
            lookup[name] = value
    return model.objects.filter(**lookup).first()


def file_fields(model):
    return [f.name for f in model._meta.concrete_fields if isinstance(f, models.FileField)]


# ── Server: applying what a phone sends ────────────────────────────────────
def _next_invoice_numbers(category):
    from .views import _get_next_serial_number
    seq = _get_next_serial_number(category)
    stamp = timezone.now().strftime('%Y%m')
    prefix = category.invoice_prefix or 'INV'
    return '%s-%s-%04d' % (prefix, stamp, seq), 'CHL-%s-%s-%04d' % (prefix, stamp, seq)


def server_apply(record):
    """Apply one record pushed by a phone. Returns a result dict for it."""
    model = MODEL_BY_NAME.get(record.get('model'))
    if model is None:
        return {'status': 'rejected', 'reason': 'unknown record type'}
    uid, data = record.get('uuid'), record.get('fields') or {}
    existing = model.objects.filter(uuid=uid).first()

    if existing is None:
        if SyncTombstone.objects.filter(model=model.__name__, uuid=uid).exists():
            return {'status': 'deleted'}        # it was deleted on the live site
        twin = _natural_match(model, data)
        if twin is not None:
            # Same category/product made on both sides: keep the server's and
            # tell the phone to treat its copy as that record.
            return {'status': 'adopt', 'adopt_uuid': str(twin.uuid)}
        obj = model(uuid=uid)
    else:
        if existing.sync_rev != int(record.get('rev') or 0):
            return {'status': 'conflict'}       # changed on the live site since the phone last saw it
        obj = existing

    try:
        with transaction.atomic():
            m2m = _assign(obj, data)
            if model is Transaction and existing is None and obj.invoice_number and obj.transaction_category_id:
                # Two phones offline can pick the same next number; the live
                # server has the last word on numbering.
                if Transaction.objects.filter(invoice_number=obj.invoice_number).exists():
                    obj.invoice_number, obj.challan_number = _next_invoice_numbers(obj.transaction_category)
            _save(obj, m2m)
    except Rejected as exc:
        return {'status': 'rejected', 'reason': str(exc)}
    except IntegrityError:
        return {'status': 'rejected', 'reason': 'it clashes with a record that already exists on the live site'}
    return {'status': 'ok', 'rev': obj.sync_rev}


# ── Backups of the live database ───────────────────────────────────────────
def backup_dir():
    path = getattr(settings, 'SYNC_BACKUP_DIR', None) or os.path.join(str(settings.BASE_DIR), 'backups')
    os.makedirs(path, exist_ok=True)
    return path


def make_backup(label, force=False):
    """Copy the database into the backups folder before sync changes it.

    Without ``force`` at most one backup a day is made. Returns the file path,
    or None when no new backup was needed (or the database is not SQLite).
    """
    if connection.vendor != 'sqlite':
        return None
    folder = backup_dir()
    existing = sorted(f for f in os.listdir(folder) if f.endswith('.sqlite3'))
    if not force and existing:
        newest = os.path.getmtime(os.path.join(folder, existing[-1]))
        if time.time() - newest < BACKUP_EVERY_SECONDS:
            return None
    name = '%s-%s.sqlite3' % (timezone.now().strftime('%Y%m%d-%H%M%S'), label)
    target = os.path.join(folder, name)
    source = sqlite3.connect(str(settings.DATABASES['default']['NAME']))
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    for old in sorted(f for f in os.listdir(folder) if f.endswith('.sqlite3'))[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return target


# ── Server: the HTTP API ───────────────────────────────────────────────────
def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _device(request):
    header = request.headers.get('Authorization', '')
    if not header.startswith('Token '):
        return None
    device = SyncDevice.objects.select_related('user').filter(token_hash=_hash(header[6:].strip())).first()
    if device is None or not device.user.is_active or not _may_sync(device.user):
        return None
    SyncDevice.objects.filter(pk=device.pk).update(last_seen=timezone.now())
    return device


def _may_sync(user):
    # A phone holds a full copy of the data, logins included: owners only.
    return user.is_superuser or getattr(user, 'role', '') == 'OWNER'


def _denied():
    return JsonResponse({'error': 'Not signed in. Connect this phone to the live server again.'}, status=401)


def _users_payload():
    User = get_user_model()
    out = []
    for user in User.objects.all():
        row = {}
        for field in User._meta.concrete_fields:
            if field.name not in USER_FIELDS_SKIP:
                row[field.name] = _plain(field.value_from_object(user))
        out.append(row)
    return out


@csrf_exempt
@require_POST
def api_login(request):
    """Exchange a username and password for this phone's sync token."""
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'invalid request'}, status=400)
    user = authenticate(request, username=data.get('username') or '', password=data.get('password') or '')
    if user is None:
        time.sleep(1)       # slow down password guessing
        return JsonResponse({'error': 'That username and password do not match.'}, status=403)
    if not _may_sync(user):
        return JsonResponse({'error': 'Only the owner account can connect a phone to the live server.'}, status=403)
    token = secrets.token_urlsafe(32)
    SyncDevice.objects.create(user=user, name=str(data.get('device') or 'Phone')[:120], token_hash=_hash(token))
    return JsonResponse({'ok': True, 'token': token, 'protocol': PROTOCOL, 'username': user.username})


@csrf_exempt
@require_GET
def api_snapshot(request):
    """The whole live database as one file: how a phone gets its first copy."""
    if _device(request) is None:
        return _denied()
    if connection.vendor != 'sqlite':
        return JsonResponse({'error': 'The live server does not use SQLite, so it cannot send a full copy.'}, status=501)
    from .mobile_views import _DeleteOnClose, _sqlite_snapshot
    make_backup('before-phone-connect')
    return FileResponse(_DeleteOnClose(_sqlite_snapshot()), content_type='application/vnd.sqlite3')


@csrf_exempt
@require_POST
def api_exchange(request):
    """One round of sync: take the phone's changes, return the server's."""
    device = _device(request)
    if device is None:
        return _denied()
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'invalid request'}, status=400)
    since = int(data.get('since') or 0)
    incoming = data.get('changes') or []
    deletes = data.get('deletes') or []

    backup = None
    if incoming or deletes:
        backup = make_backup('before-sync')     # never change live data without a recent backup

    results = []
    order = {m.__name__: i for i, m in enumerate(SYNC_MODELS)}
    for record in sorted(incoming, key=lambda r: order.get(r.get('model'), 99)):
        result = server_apply(record)
        result.update(model=record.get('model'), uuid=record.get('uuid'))
        results.append(result)

    for entry in deletes:
        model = MODEL_BY_NAME.get(entry.get('model'))
        if model is not None:
            for obj in model.objects.filter(uuid=entry.get('uuid')):
                obj.delete()                    # records its own tombstone for other phones

    upto = SyncCounter.current()
    changes = []
    for model in SYNC_MODELS:
        for obj in model.objects.filter(sync_rev__gt=since, sync_rev__lte=upto):
            changes.append(serialize(obj))
    tombstones = [{'model': t.model, 'uuid': str(t.uuid)}
                  for t in SyncTombstone.objects.filter(sync_rev__gt=since, sync_rev__lte=upto)]
    return JsonResponse({
        'ok': True, 'protocol': PROTOCOL, 'cursor': upto, 'results': results,
        'changes': changes, 'deletes': tombstones, 'users': _users_payload(),
        'backup': os.path.basename(backup) if backup else None,
    })


def _safe_media_path(name):
    root = os.path.realpath(str(settings.MEDIA_ROOT))
    path = os.path.realpath(os.path.join(root, name or ''))
    if not name or not path.startswith(root + os.sep):
        return None
    return path


@csrf_exempt
def api_media(request):
    """Images that belong to records (signatures, seals, requisition photos)."""
    if _device(request) is None:
        return _denied()
    name = request.GET.get('path') or request.POST.get('path') or ''
    path = _safe_media_path(name)
    if path is None:
        return JsonResponse({'error': 'bad path'}, status=400)
    if request.method == 'GET':
        if not os.path.isfile(path):
            return HttpResponse(status=404)
        return FileResponse(open(path, 'rb'))
    upload = request.FILES.get('file')
    if upload is None:
        return JsonResponse({'error': 'no file'}, status=400)
    if not os.path.exists(path):                # never replace a file the server already has
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            for chunk in upload.chunks():
                fh.write(chunk)
    return JsonResponse({'ok': True})
