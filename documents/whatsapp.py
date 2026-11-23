"""WhatsApp Business Platform (Cloud API): receiving and sending messages.

Setup (server ``.env``; all values come from developers.facebook.com, under
your app's WhatsApp section):

    WHATSAPP_TOKEN=...              # permanent access token (System User)
    WHATSAPP_PHONE_NUMBER_ID=...    # the business number's id (not the number)
    WHATSAPP_APP_SECRET=...         # app secret, to check webhook signatures
    WHATSAPP_VERIFY_TOKEN=...       # any phrase; type the same one in Meta
    WHATSAPP_API_VERSION=v23.0      # Graph API version shown in Meta's dashboard
    OWNER_WHATSAPP=8801XXXXXXXXX    # your own number, for questions to you

Webhook URL to give Meta: https://<your-domain>/api/whatsapp/webhook/
(subscribe to the "messages" field).

Without these, nothing is sent through the API: messages wait in the order's
outbox and are sent with one tap from the app.
"""
import hashlib
import hmac
import json
import logging
import mimetypes
import threading
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.files.base import ContentFile
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import ledger
from .models import InboxMessage, OutgoingMessage

logger = logging.getLogger(__name__)
PLACEHOLDER = 'PASTE-HERE'


def _setting(name):
    value = (getattr(settings, name, '') or '').strip()
    return '' if value.startswith('PASTE') else value


def is_configured():
    return bool(_setting('WHATSAPP_TOKEN') and _setting('WHATSAPP_PHONE_NUMBER_ID'))


def owner_number():
    return ''.join(ch for ch in _setting('OWNER_WHATSAPP') if ch.isdigit())


def _graph(path):
    return 'https://graph.facebook.com/%s/%s' % (_setting('WHATSAPP_API_VERSION') or 'v23.0', path.lstrip('/'))


def _request(method, url, **kwargs):
    import requests
    headers = kwargs.pop('headers', {})
    headers['Authorization'] = 'Bearer ' + _setting('WHATSAPP_TOKEN')
    return requests.request(method, url, headers=headers, timeout=30, **kwargs)


# ── Sending ────────────────────────────────────────────────────────────────
class SendError(Exception):
    pass


def _explain(payload):
    error = (payload or {}).get('error') or {}
    code = error.get('code')
    if code == 131047:
        return ('More than 24 hours have passed since the client last wrote, so WhatsApp only allows an '
                'approved template message. Send this one by hand.')
    return error.get('message') or 'WhatsApp refused the message.'


def send_text(phone, text):
    if not is_configured():
        raise SendError('WhatsApp Business API is not set up.')
    response = _request('POST', _graph('%s/messages' % _setting('WHATSAPP_PHONE_NUMBER_ID')), json={
        'messaging_product': 'whatsapp', 'to': phone, 'type': 'text',
        'text': {'body': text[:4096], 'preview_url': False},
    })
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        raise SendError(_explain(payload))
    return ((payload.get('messages') or [{}])[0]).get('id', '')


def send_document(phone, filename, content, caption=''):
    if not is_configured():
        raise SendError('WhatsApp Business API is not set up.')
    upload = _request('POST', _graph('%s/media' % _setting('WHATSAPP_PHONE_NUMBER_ID')),
                      data={'messaging_product': 'whatsapp', 'type': 'application/pdf'},
                      files={'file': (filename, content, 'application/pdf')})
    if upload.status_code >= 400:
        raise SendError(_explain(upload.json() if upload.content else {}))
    media_id = upload.json().get('id')
    response = _request('POST', _graph('%s/messages' % _setting('WHATSAPP_PHONE_NUMBER_ID')), json={
        'messaging_product': 'whatsapp', 'to': phone, 'type': 'document',
        'document': {'id': media_id, 'filename': filename, 'caption': caption[:1024]},
    })
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        raise SendError(_explain(payload))
    return ((payload.get('messages') or [{}])[0]).get('id', '')


# ── Outbox: every message to a client goes through here ────────────────────
def queue(order, text, created_by='assistant', hold_reason='', to_staff=False, document=''):
    """Send now when allowed, otherwise keep it for a person to approve."""
    msg = OutgoingMessage.objects.create(
        order=order, to_phone=owner_number() if to_staff else (order.phone if order else ''),
        to_staff=to_staff, text=text, document=document, created_by=created_by, reason=hold_reason)
    if not hold_reason and not document and is_configured() and msg.to_phone:
        try:
            dispatch(msg)
        except SendError:
            pass
    elif order is not None and hold_reason:
        ledger.record(order, 'system', 'system', 'Message to %s waiting for approval: %s'
                      % ('staff' if to_staff else 'client', hold_reason),
                      {'outgoing': msg.pk, 'text': text, 'document': document})
    return msg


def dispatch(msg, user=None):
    """Send a queued message through the API and record it."""
    try:
        msg.wa_id = send_text(msg.to_phone, msg.text)
    except Exception as exc:
        msg.status, msg.error = OutgoingMessage.FAILED, str(exc)[:255]
        msg.save(update_fields=['status', 'error'])
        if msg.order:
            ledger.record(msg.order, 'system', 'system', 'Sending failed: %s' % msg.error,
                          {'outgoing': msg.pk}, user=user)
        raise SendError(msg.error)
    mark_sent(msg, 'whatsapp_api', user=user)


def mark_sent(msg, channel, user=None):
    msg.status, msg.sent_at, msg.channel = OutgoingMessage.SENT, timezone.now(), channel
    msg.save(update_fields=['status', 'sent_at', 'channel', 'wa_id'])
    if msg.order:
        who = 'staff' if msg.to_staff else 'client'
        ledger.record(msg.order, 'message_out', 'staff' if user else msg.created_by if msg.created_by in
                      ('assistant', 'system', 'staff') else 'system',
                      'Message to %s%s: %s' % (who, '' if channel == 'whatsapp_api' else ' (sent by hand)', msg.text),
                      {'outgoing': msg.pk, 'to': msg.to_phone, 'channel': channel, 'wa_id': msg.wa_id,
                       'document': msg.document}, user=user)


# ── Receiving (webhook) ────────────────────────────────────────────────────
def _signature_ok(request):
    secret = _setting('WHATSAPP_APP_SECRET')
    if not secret:
        return False
    sent = request.headers.get('X-Hub-Signature-256', '')
    expected = 'sha256=' + hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent, expected)


def _download_media(media_id):
    meta = _request('GET', _graph(media_id))
    if meta.status_code >= 400:
        return None, ''
    info = meta.json()
    data = _request('GET', info.get('url', ''))
    if data.status_code >= 400:
        return None, ''
    return data.content, info.get('mime_type', '')


def _text_of(message):
    kind = message.get('type')
    if kind == 'text':
        return (message.get('text') or {}).get('body', '')
    if kind in ('image', 'document', 'video'):
        return (message.get(kind) or {}).get('caption', '') or ''
    if kind == 'interactive':
        reply = message.get('interactive') or {}
        return ((reply.get('button_reply') or reply.get('list_reply') or {}).get('title', ''))
    if kind == 'button':
        return (message.get('button') or {}).get('text', '')
    return ''


def receive(payload):
    """Store every message and delivery receipt in a webhook payload."""
    from .inbox_views import run_order_engine
    stored = 0
    for entry in payload.get('entry', []):
        for change in entry.get('changes', []):
            value = change.get('value') or {}
            names = {c.get('wa_id'): (c.get('profile') or {}).get('name', '') for c in value.get('contacts', [])}
            for message in value.get('messages', []):
                wa_id = message.get('id', '')
                if not wa_id or InboxMessage.objects.filter(wa_id=wa_id).exists():
                    continue
                phone = message.get('from', '')
                when = datetime.fromtimestamp(int(message.get('timestamp') or 0), tz=dt_timezone.utc)
                text = _text_of(message).strip()
                msg = InboxMessage(external_id=hashlib.sha1(wa_id.encode()).hexdigest(), source='notification',
                                   app='WhatsApp Business API', sender=names.get(phone) or phone, phone=phone,
                                   wa_id=wa_id, received_at=when, text=text)
                kind = message.get('type')
                if kind in ('image', 'document'):
                    content, mime = _download_media((message.get(kind) or {}).get('id', ''))
                    if content:
                        ext = mimetypes.guess_extension(mime or '') or '.bin'
                        msg.media.save('%s%s' % (wa_id[-16:], ext), ContentFile(content), save=False)
                        msg.media_type = mime
                    if not msg.text:
                        msg.text = '[photo]' if kind == 'image' else '[document]'
                if not msg.text:
                    msg.text = '[%s message]' % (kind or 'unsupported')
                msg.save()
                stored += 1
            for status in value.get('statuses', []):
                _receipt(status)
    if stored:
        threading.Thread(target=run_order_engine, name='nrs-orders', daemon=True).start()
    return stored


def _receipt(status):
    """'delivered' and 'read' receipts are written into the order record."""
    if status.get('status') not in ('delivered', 'read', 'failed'):
        return
    msg = OutgoingMessage.objects.filter(wa_id=status.get('id', '')).select_related('order').first()
    if msg is None or msg.order is None:
        return
    when = datetime.fromtimestamp(int(status.get('timestamp') or 0), tz=dt_timezone.utc)
    label = {'delivered': 'delivered to', 'read': 'read by', 'failed': 'could not be delivered to'}[status['status']]
    ledger.record(msg.order, 'system', 'system', 'WhatsApp: message %s the %s at %s.'
                  % (label, 'staff member' if msg.to_staff else 'client', timezone.localtime(when).strftime('%d %b %Y %H:%M')),
                  {'outgoing': msg.pk, 'wa_id': msg.wa_id, 'status': status['status'], 'at': when.isoformat()})


@csrf_exempt
def webhook(request):
    if request.method == 'GET':
        token = _setting('WHATSAPP_VERIFY_TOKEN')
        if token and request.GET.get('hub.mode') == 'subscribe' and request.GET.get('hub.verify_token') == token:
            return HttpResponse(request.GET.get('hub.challenge', ''))
        return HttpResponseForbidden('verification failed')
    if request.method != 'POST':
        return HttpResponse(status=405)
    if not _signature_ok(request):
        return HttpResponseForbidden('bad signature')
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return HttpResponse(status=400)
    try:
        receive(payload)
    except Exception:
        logger.exception('whatsapp webhook failed')
    return JsonResponse({'ok': True})     # always 200, or Meta keeps retrying
