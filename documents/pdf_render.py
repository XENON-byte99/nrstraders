"""Make a bill's PDFs on the server, from the existing print pages.

The server opens its own print page (invoice in the supplier's chosen layout,
challan, quotation or mushok) in a headless browser, signed in as the owner,
and runs the page's own "Download PDF" code. The PDF is therefore exactly the
one the Download PDF button gives: same layouts, same saved print tweaks.

Needs Chromium on the server (one-time, done by the deploy workflow):

    pip install playwright
    python -m playwright install --with-deps chromium
"""
import base64
import hashlib
import logging

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db.models import Q

logger = logging.getLogger(__name__)
DOCS = ('invoice', 'challan', 'quotation', 'mushok')


class RenderError(Exception):
    pass


def available():
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _owner():
    User = get_user_model()
    return (User.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(role='OWNER')).order_by('id').first())


def render(bill, doc):
    """Return (filename, pdf_bytes) for one document of ``bill``."""
    if doc not in DOCS:
        raise RenderError('Unknown document: %s' % doc)
    if not available():
        raise RenderError('PDF maker is not installed on the server (pip install playwright, then '
                          'python -m playwright install --with-deps chromium).')
    user = _owner()
    if user is None:
        raise RenderError('No owner account to open the print pages with.')
    base = (getattr(settings, 'SITE_URL', '') or 'http://127.0.0.1:8000').rstrip('/')

    # A short-lived sign-in just for this render, deleted straight after.
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            # PDF_CHROMIUM_PATH: use an already-installed Chromium instead of Playwright's own.
            executable = getattr(settings, 'PDF_CHROMIUM_PATH', '') or None
            browser = pw.chromium.launch(args=['--no-sandbox'], executable_path=executable)
            try:
                context = browser.new_context(viewport={'width': 1280, 'height': 900})
                context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': session.session_key, 'url': base}])
                page = context.new_page()
                # Catch the PDF the page hands over, as the Android app does.
                page.add_init_script(
                    'window.NRSApp = { sendToWhatsApp: function (name, data) { window.__nrsPdf = {name: name, data: data}; } };')
                page.goto('%s/bills/%d/print/%s/?send=whatsapp' % (base, bill.pk, doc), wait_until='networkidle',
                          timeout=60000)
                if '/login' in page.url:
                    raise RenderError('The print page asked for a sign-in; check SITE_URL.')
                page.wait_for_function('window.__nrsPdf', timeout=90000)
                result = page.evaluate('window.__nrsPdf')
            finally:
                browser.close()
    except RenderError:
        raise
    except Exception as exc:
        logger.exception('PDF render failed')
        raise RenderError('Could not make the %s PDF: %s' % (doc, exc))
    finally:
        session.delete()
    data = base64.b64decode(result['data'])
    if not data.startswith(b'%PDF'):
        raise RenderError('The %s page did not produce a PDF.' % doc)
    return result['name'], data


def sha256(data):
    return hashlib.sha256(data).hexdigest()
