"""First-run setup and backup/restore.

The mobile app keeps everything on the phone, so two things matter that the
hosted website never needed: creating the first account without a server
admin, and getting data in and out as a single file.
"""
import json
import os
import sqlite3
import tempfile

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.db import connection, connections
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.utils import log_audit
from .models import TransactionCategory

_has_users = False      # cached once true; reset after a restore
REQUIRED_TABLES = {'accounts_user', 'documents_transaction', 'documents_transactionitem'}
JSON_EXCLUDE = ['contenttypes', 'auth.permission', 'sessions', 'admin.logentry']


def _users_exist():
    global _has_users
    if not _has_users:
        _has_users = get_user_model().objects.exists()
    return _has_users


def _is_mobile():
    return bool(getattr(settings, 'MOBILE_APP', False))


def _is_sqlite():
    return connection.vendor == 'sqlite'


def _can_manage_backups(user):
    return user.is_authenticated and (user.is_superuser or getattr(user, 'role', '') == 'OWNER')


class FirstRunMiddleware:
    """Mobile only: a brand-new install goes to the setup screen, not a login
    form nobody has a password for."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not (path.startswith('/setup/') or path.startswith('/static/')) and not _users_exist():
            return redirect('mobile_setup')
        return self.get_response(request)


# ── First run ──────────────────────────────────────────────────────────────
def mobile_setup(request):
    if not _is_mobile():
        raise Http404
    if _users_exist():
        return redirect('login')

    values = {'full_name': '', 'username': ''}
    if request.method == 'POST' and request.POST.get('mode') == 'connect':
        from . import sync_client
        try:
            sync_client.connect(request.POST.get('server'), request.POST.get('username'), request.POST.get('password'))
        except sync_client.SyncError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Connected. This phone now holds a copy of your live data and will keep it '
                                      'in step. Sign in with your website username and password.')
            return redirect('login')
    elif request.method == 'POST' and request.POST.get('mode') == 'restore':
        upload = request.FILES.get('backup')
        if not upload:
            messages.error(request, 'Choose a backup file first.')
        else:
            try:
                _restore(upload)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, 'Backup restored. Sign in with your existing username and password.')
                return redirect('login')
    elif request.method == 'POST':
        values['full_name'] = (request.POST.get('full_name') or '').strip()
        values['username'] = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        if not values['username']:
            messages.error(request, 'Choose a username.')
        elif len(password) < 4:
            messages.error(request, 'Use a password of at least 4 characters.')
        elif password != (request.POST.get('password2') or ''):
            messages.error(request, 'The two passwords do not match.')
        else:
            User = get_user_model()
            user = User(username=values['username'], first_name=values['full_name'][:150],
                        role='OWNER', is_staff=True, is_superuser=True)
            for field in User._meta.fields:
                if field.name.startswith('p_'):
                    setattr(user, field.name, True)
            user.set_password(password)
            user.save()
            if not TransactionCategory.objects.exists():
                TransactionCategory.objects.create(name='General', invoice_prefix='INV')
            global _has_users
            _has_users = True
            login(request, user)
            messages.success(request, 'Your account is ready. Everything you enter stays on this device.')
            return redirect('home')
    return render(request, 'documents/mobile_setup.html', {
        'values': values, 'default_server': getattr(settings, 'LIVE_SERVER_URL', '')})


# ── Backup ─────────────────────────────────────────────────────────────────
def _sqlite_snapshot():
    """A consistent copy of the live database, safe to take while in use."""
    fd, path = tempfile.mkstemp(suffix='.sqlite3')
    os.close(fd)
    source = sqlite3.connect(settings.DATABASES['default']['NAME'])
    target = sqlite3.connect(path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return path


class _DeleteOnClose:
    def __init__(self, path):
        self.path, self.fh = path, open(path, 'rb')

    def read(self, size=-1):
        return self.fh.read(size)

    def close(self):
        self.fh.close()
        try:
            os.remove(self.path)
        except OSError:
            pass


@login_required
def backup_page(request):
    if not _can_manage_backups(request.user):
        messages.error(request, 'Only the owner can back up or restore data.')
        return redirect('home')
    size = None
    if _is_sqlite():
        try:
            size = os.path.getsize(settings.DATABASES['default']['NAME'])
        except OSError:
            size = None
    return render(request, 'documents/backup.html', {
        'is_sqlite': _is_sqlite(), 'db_size': size, 'is_mobile': _is_mobile(),
    })


@login_required
def backup_download(request):
    if not _can_manage_backups(request.user):
        raise Http404
    stamp = timezone.localtime().strftime('%Y%m%d-%H%M')
    log_audit(request.user, 'Backup', 'Downloaded a full data backup.')
    if _is_sqlite():
        response = FileResponse(_DeleteOnClose(_sqlite_snapshot()), as_attachment=True,
                                filename='nrs-backup-%s.sqlite3' % stamp,
                                content_type='application/vnd.sqlite3')
        return response
    from io import StringIO
    buffer = StringIO()
    call_command('dumpdata', natural_foreign=True, natural_primary=True, exclude=JSON_EXCLUDE, stdout=buffer)
    response = HttpResponse(buffer.getvalue(), content_type='application/json')
    response['Content-Disposition'] = 'attachment; filename="nrs-backup-%s.json"' % stamp
    return response


# ── Restore ────────────────────────────────────────────────────────────────
def _restore(upload):
    """Replace all data with the uploaded backup. Raises ValueError with a
    message for the user when the file can't be used."""
    global _has_users
    head = upload.read(16)
    upload.seek(0)
    if head.startswith(b'SQLite format 3'):
        if not _is_sqlite():
            raise ValueError('This site does not use SQLite, so it cannot load a .sqlite3 backup. '
                             'Use a .json backup instead.')
        _restore_sqlite(upload)
    elif head.lstrip()[:1] in (b'[', b'{'):
        _restore_json(upload)
    else:
        raise ValueError('That is not a backup file. Choose a .sqlite3 or .json backup made by this system.')
    _has_users = False


def _restore_sqlite(upload):
    db_path = str(settings.DATABASES['default']['NAME'])
    incoming = db_path + '.incoming'
    with open(incoming, 'wb') as fh:
        for chunk in upload.chunks():
            fh.write(chunk)
    try:
        check = sqlite3.connect(incoming)
        try:
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            ok = check.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        finally:
            check.close()
    except sqlite3.DatabaseError:
        os.remove(incoming)
        raise ValueError('That file is damaged and could not be read.')
    if not ok or not REQUIRED_TABLES.issubset(tables):
        os.remove(incoming)
        raise ValueError('That database is not an NRS billing backup.')

    connections.close_all()
    previous = db_path + '.before-restore'
    had_previous = os.path.exists(db_path)
    if had_previous:
        os.replace(db_path, previous)
    for suffix in ('-wal', '-shm'):
        if os.path.exists(db_path + suffix):
            os.remove(db_path + suffix)
    os.replace(incoming, db_path)
    try:
        call_command('migrate', interactive=False, verbosity=0)   # older backups: bring the schema up to date
    except Exception as exc:
        connections.close_all()
        if had_previous:
            os.replace(previous, db_path)
        raise ValueError('The backup could not be upgraded to this version (%s). Your data was left unchanged.' % exc)


def _restore_json(upload):
    try:
        data = json.loads(upload.read().decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError):
        raise ValueError('That JSON file could not be read.')
    if not isinstance(data, list) or not any(isinstance(o, dict) and o.get('model') == 'accounts.user' for o in data):
        raise ValueError('That JSON file is not an NRS billing backup.')
    fd, path = tempfile.mkstemp(suffix='.json')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(data, fh)
    from django.db import transaction
    try:
        with transaction.atomic():
            call_command('flush', interactive=False, verbosity=0)
            call_command('loaddata', path, verbosity=0)
    except Exception as exc:
        raise ValueError('The backup could not be loaded (%s). Your data was left unchanged.' % exc)
    finally:
        os.remove(path)


@login_required
@require_POST
def backup_restore(request):
    if not _can_manage_backups(request.user):
        raise Http404
    upload = request.FILES.get('backup')
    if not upload:
        messages.error(request, 'Choose a backup file first.')
        return redirect('backup_page')
    try:
        _restore(upload)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('backup_page')
    # Accounts were replaced along with everything else, so sign in again.
    request.session.flush()
    messages.success(request, 'Backup restored. Sign in with the username and password from that backup.')
    return redirect('login')
