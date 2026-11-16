"""The phone's half of live-server sync (see live_sync.py for the design)."""
import io
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as uuidlib

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import live_sync
from .live_sync import MODEL_BY_NAME, SYNC_MODELS, Rejected, applying_remote, file_fields, serialize
from .models import SyncCounter, SyncDevice, SyncTombstone

_lock = threading.Lock()
AUTO_EVERY_SECONDS = 45          # how often the app looks for something to send
PULL_EVERY_SECONDS = 300         # fetch server changes at least this often


class SyncError(Exception):
    """Something the user should read (wrong password, no internet, ...)."""


# ── Saved connection details ───────────────────────────────────────────────
def _config_path():
    return os.path.join(str(settings.DATA_DIR), 'sync.json')


def load_config():
    try:
        with open(_config_path(), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_config(config):
    tmp = _config_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(config, fh)
    os.replace(tmp, _config_path())


def is_connected():
    return bool(load_config().get('token'))


def pending_count():
    total = sum(m.objects.filter(is_dirty=True).count() for m in SYNC_MODELS)
    return total + SyncTombstone.objects.filter(is_dirty=True).count()


def status():
    config = load_config()
    return {
        'connected': bool(config.get('token')),
        'server': config.get('server', ''),
        'username': config.get('username', ''),
        'last_sync': config.get('last_sync'),
        'last_error': config.get('last_error', ''),
        'problems': config.get('problems', []),
        'pending': pending_count() if config.get('token') else 0,
    }


# ── HTTP ───────────────────────────────────────────────────────────────────
def normalise_server(server):
    server = (server or '').strip().rstrip('/')
    if server and '://' not in server:
        server = 'https://' + server
    return server


def _open(server, path, token=None, body=None, headers=None, timeout=90):
    request = urllib.request.Request(server + path, data=body, headers=headers or {})
    if token:
        request.add_header('Authorization', 'Token ' + token)
    request.add_header('User-Agent', 'NRS-Billing-App')
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode('utf-8', 'replace')).get('error')
        except Exception:
            message = None
        if exc.code == 404 and not message:
            message = 'That server does not support phone sync yet. Update the website first.'
        raise SyncError(message or 'The live server answered with an error (%s).' % exc.code)
    except (urllib.error.URLError, OSError, ValueError):
        raise SyncError('Could not reach the live server. Check the address and your internet connection.')


def _post_json(server, path, payload, token=None):
    with _open(server, path, token, json.dumps(payload).encode(), {'Content-Type': 'application/json'}) as response:
        return json.loads(response.read().decode('utf-8'))


# ── Images that belong to records ──────────────────────────────────────────
def _media_path(name):
    return os.path.join(str(settings.MEDIA_ROOT), name)


def _download_media(config, name):
    if not name or os.path.exists(_media_path(name)):
        return
    try:
        with _open(config['server'], '/api/sync/media/?path=' + urllib.parse.quote(name), config['token']) as response:
            data = response.read()
    except SyncError:
        return                                   # missing on the server too; try again next time
    os.makedirs(os.path.dirname(_media_path(name)), exist_ok=True)
    with open(_media_path(name), 'wb') as fh:
        fh.write(data)


def _upload_media(config, name):
    path = _media_path(name)
    if not name or not os.path.isfile(path):
        return
    boundary = uuidlib.uuid4().hex
    with open(path, 'rb') as fh:
        content = fh.read()
    body = b''.join([
        ('--%s\r\nContent-Disposition: form-data; name="path"\r\n\r\n%s\r\n' % (boundary, name)).encode(),
        ('--%s\r\nContent-Disposition: form-data; name="file"; filename="f"\r\n'
         'Content-Type: application/octet-stream\r\n\r\n' % boundary).encode(),
        content, ('\r\n--%s--\r\n' % boundary).encode(),
    ])
    with _open(config['server'], '/api/sync/media/', config['token'], body,
               {'Content-Type': 'multipart/form-data; boundary=' + boundary}):
        pass


def _fetch_all_media(config):
    for model in SYNC_MODELS:
        for name in file_fields(model):
            for value in model.objects.exclude(**{name: ''}).values_list(name, flat=True):
                if value:
                    _download_media(config, value)


# ── Connecting a phone ─────────────────────────────────────────────────────
class _Upload:
    """Just enough of an uploaded file for the restore code."""

    def __init__(self, path):
        self.path = path

    def chunks(self, size=1024 * 1024):
        with open(self.path, 'rb') as fh:
            while True:
                block = fh.read(size)
                if not block:
                    return
                yield block


def connect(server, username, password):
    """Sign in to the live server and replace this phone's data with a full
    copy of the live data. The phone's previous data is backed up first."""
    from . import mobile_views

    server = normalise_server(server)
    if not server:
        raise SyncError('Enter the address of your live website.')
    with _lock:
        login = _post_json(server, '/api/sync/login/', {'username': username, 'password': password,
                                                        'device': 'NRS Billing app'})
        token = login['token']
        fd, snapshot = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        try:
            with _open(server, '/api/sync/snapshot/', token, timeout=300) as response, open(snapshot, 'wb') as out:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    out.write(block)
            live_sync.make_backup('phone-before-connect', force=True)
            try:
                mobile_views._restore_sqlite(_Upload(snapshot))
            except ValueError as exc:
                raise SyncError(str(exc))
        finally:
            if os.path.exists(snapshot):
                os.remove(snapshot)

        # This copy now belongs to a phone: nothing is waiting to be sent, and
        # the server's own list of connected phones does not belong here.
        SyncDevice.objects.all().delete()
        SyncTombstone.objects.all().delete()
        for model in SYNC_MODELS:
            model.objects.update(is_dirty=False)
        mobile_views._has_users = False
        config = {'server': server, 'token': token, 'username': login.get('username', username),
                  'cursor': SyncCounter.current(), 'last_sync': timezone.now().isoformat(),
                  'last_error': '', 'problems': []}
        save_config(config)
        _fetch_all_media(config)
        return config


def dismiss_problems():
    config = load_config()
    config['problems'] = []
    save_config(config)


def disconnect():
    """Stop syncing. The data stays on the phone."""
    with _lock:
        if os.path.exists(_config_path()):
            os.remove(_config_path())


# ── One round of sync ──────────────────────────────────────────────────────
def _label(model_name, obj):
    return '%s "%s"' % (model_name, str(obj)[:60])


def _apply_users(users):
    User = get_user_model()
    for row in users or []:
        user = User.objects.filter(username=row.get('username')).first() or User(username=row.get('username'))
        for field in User._meta.concrete_fields:
            if field.name in row and field.name not in ('id', 'username'):
                setattr(user, field.attname, field.to_python(row[field.name]))
        user.save()


def _apply_change(record, problems):
    model = MODEL_BY_NAME.get(record.get('model'))
    if model is None:
        return
    local = model.objects.filter(uuid=record['uuid']).first()
    if local is not None and local.is_dirty:
        return                      # edited here meanwhile; it goes up on the next round
    obj = local or model(uuid=record['uuid'])
    try:
        with transaction.atomic():
            m2m = live_sync._assign(obj, record.get('fields') or {})
            live_sync._save(obj, m2m, rev=record.get('rev'))
    except Rejected as exc:
        problems.append('Could not receive a %s from the live site: %s.' % (model._meta.verbose_name, exc))
    except IntegrityError:
        problems.append('Could not receive %s: it clashes with a record on this phone.' % _label(model.__name__, obj))


def sync_now():
    """Send local changes, receive the server's. Returns a short summary."""
    if not _lock.acquire(blocking=False):
        return {'ok': True, 'busy': True}
    try:
        config = load_config()
        if not config.get('token'):
            raise SyncError('This phone is not connected to a live server.')
        problems = []

        outgoing, sent = [], {}
        for model in SYNC_MODELS:
            for obj in model.objects.filter(is_dirty=True):
                outgoing.append(serialize(obj))
                sent[(model.__name__, str(obj.uuid))] = (obj.pk, obj.updated_at)
                for name in file_fields(model):
                    value = getattr(obj, name).name
                    if value:
                        try:
                            _upload_media(config, value)
                        except SyncError:
                            pass
        tombstones = list(SyncTombstone.objects.filter(is_dirty=True))
        deletes = [{'model': t.model, 'uuid': str(t.uuid)} for t in tombstones]

        try:
            reply = _post_json(config['server'], '/api/sync/exchange/',
                               {'since': config.get('cursor', 0), 'changes': outgoing, 'deletes': deletes},
                               config['token'])
        except SyncError as exc:
            config['last_error'] = str(exc)
            save_config(config)
            raise

        conflicts = 0
        with applying_remote():
            for result in reply.get('results', []):
                model = MODEL_BY_NAME.get(result.get('model'))
                key = (result.get('model'), result.get('uuid'))
                if model is None or key not in sent:
                    continue
                pk, stamp = sent[key]
                rows = model.objects.filter(pk=pk)
                outcome = result.get('status')
                if outcome == 'ok':
                    # Only "clean" if it was not edited again while we were talking.
                    rows.filter(updated_at=stamp).update(is_dirty=False)
                    rows.update(sync_rev=result.get('rev') or 0)
                elif outcome == 'adopt':
                    rows.update(uuid=result['adopt_uuid'], is_dirty=False)
                elif outcome == 'conflict':
                    conflicts += 1
                    rows.update(is_dirty=False)         # the live version arrives below
                elif outcome == 'deleted':
                    for obj in rows:
                        problems.append('%s was deleted on the live site, so your change to it was dropped.'
                                        % _label(key[0], obj))
                        obj.delete()
                else:
                    obj = rows.first()
                    problems.append('Could not send %s: %s.' % (_label(key[0], obj) if obj else key[0],
                                                               result.get('reason') or 'the live site refused it'))
            SyncTombstone.objects.filter(pk__in=[t.pk for t in tombstones]).delete()

            _apply_users(reply.get('users'))
            order = {m.__name__: i for i, m in enumerate(SYNC_MODELS)}
            for record in sorted(reply.get('changes', []), key=lambda r: order.get(r.get('model'), 99)):
                _apply_change(record, problems)
            for entry in reply.get('deletes', []):
                model = MODEL_BY_NAME.get(entry.get('model'))
                if model is not None:
                    for obj in model.objects.filter(uuid=entry.get('uuid'), is_dirty=False):
                        obj.delete()

        if conflicts:
            problems.append('%d record%s had also been changed on the live site; the live version was kept.'
                            % (conflicts, '' if conflicts == 1 else 's'))
        # Keep earlier notes until the user has read and dismissed them; the
        # automatic sync a minute later must not make a conflict notice vanish.
        kept = [p for p in config.get('problems', []) if p not in problems]
        config.update(cursor=reply.get('cursor', config.get('cursor', 0)), last_sync=timezone.now().isoformat(),
                      last_error='', problems=(problems + kept)[:20])
        save_config(config)
        try:
            for record in reply.get('changes', []):
                model = MODEL_BY_NAME.get(record.get('model'))
                for name in file_fields(model) if model else []:
                    _download_media(config, (record.get('fields') or {}).get(name))
        except Exception:
            pass
        return {'ok': True, 'sent': len(outgoing) + len(deletes), 'received': len(reply.get('changes', [])),
                'problems': problems}
    finally:
        _lock.release()


# ── Background sync inside the app ─────────────────────────────────────────
def auto_loop():
    """Runs for the life of the app: sends changes soon after they are made
    and checks the live server for news every few minutes."""
    from django.db import close_old_connections
    last_pull = 0.0
    while True:
        time.sleep(AUTO_EVERY_SECONDS)
        try:
            close_old_connections()
            if not is_connected():
                continue
            if pending_count() or time.time() - last_pull > PULL_EVERY_SECONDS:
                sync_now()
                last_pull = time.time()
        except SyncError:
            last_pull = time.time() - PULL_EVERY_SECONDS + 60      # offline: try again in a minute
        except Exception as exc:                                     # never let the loop die
            try:
                config = load_config()
                config['last_error'] = 'Sync stopped unexpectedly: %s' % exc
                save_config(config)
            except Exception:
                pass
        finally:
            try:
                close_old_connections()
            except Exception:
                pass


def start_auto_sync():
    threading.Thread(target=auto_loop, name='nrs-sync', daemon=True).start()
