"""AI order assistant, order record and WhatsApp channel - with Claude and
WhatsApp replaced by scripted stand-ins, so no key or network is needed."""
import hashlib
import hmac
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings

from accounts.models import User
from . import ai_client, ledger, order_agent, whatsapp
from .inbox_views import store_message
from .models import (AIUsage, BusinessParty, ClientOrder, InboxMessage, OrderEvent, OutgoingMessage, Product,
                     TransactionCategory)

STATIC = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
          'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}


def _resp(*calls, stop='tool_use', model='claude-opus-5-5', tokens=(1000, 200)):
    blocks = [SimpleNamespace(type='tool_use', id='t%d' % i, name=n, input=a) for i, (n, a) in enumerate(calls)]
    stop_reason = stop if (calls or stop == 'refusal') else 'end_turn'
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, model=model,
                           usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1],
                                                 cache_read_input_tokens=0, cache_creation_input_tokens=0,
                                                 iterations=None),
                           _request_id='req_test')


class FakeClaude:
    """Plays back a list of turns; each turn is a list of tool calls."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **params):
        self.requests.append(params)
        calls = self.turns.pop(0) if self.turns else []
        return _resp(*calls)


@override_settings(STORAGES=STATIC, ANTHROPIC_API_KEY='sk-ant-test', AUTOPILOT_ENABLED=False,
                   WHATSAPP_TOKEN='PASTE-HERE', OWNER_WHATSAPP='PASTE-HERE')
class AgentConversationTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', password='pw', role='OWNER')
        self.client.force_login(self.owner)
        self.cat = TransactionCategory.objects.create(name='Grocery Item', invoice_prefix='GRO', default_vat=Decimal('5'))
        Product.objects.create(name='Miniket Rice', category=self.cat, unit='Kg', base_price=58, upscale_value=64)
        self.rahim = BusinessParty.objects.create(party_type='BUYER', name='Rahim Store', whatsapp_number='8801711000000')

    def incoming(self, text, fake):
        msg = store_message('notification', text, 'Rahim Store', app='WhatsApp')[0]
        msg.phone = '8801711000000'
        msg.save()
        with mock.patch.object(ai_client, 'client', return_value=fake):
            order_agent.handle_new_messages()
        return ClientOrder.objects.get(key='Rahim Store')

    def test_full_conversation_is_recorded_and_checked(self):
        fake = FakeClaude([
            [('update_items', {'changes': [
                {'action': 'add', 'item_number': None, 'description': 'Miniket rice', 'quantity': 25, 'unit': 'Kg',
                 'price': None, 'price_is_final': None, 'source': 'message'},
                {'action': 'add', 'item_number': None, 'description': 'LED bulb', 'quantity': None, 'unit': 'Pcs',
                 'price': None, 'price_is_final': None, 'source': 'message'}]})],
            [('message_client', {'text': 'How many LED bulbs do you need?'}),
             ('ask_staff', {'question': 'Price for LED bulb?', 'kind': 'price'})],
            [],
        ])
        order = self.incoming('Rice 25 kg and some LED bulbs', fake)
        self.assertEqual(len(order.items), 2)
        self.assertEqual(order.items[0]['price'], 64.0)                  # filled from saved product, not by the AI
        held = OutgoingMessage.objects.get(order=order, to_staff=False)
        self.assertEqual(held.status, 'PENDING')                         # autopilot off: waits for approval
        self.assertIn('Autopilot is off', held.reason)
        self.assertEqual(AIUsage.objects.count(), 3)
        self.assertEqual(AIUsage.objects.first().cost_usd, Decimal('0.008'))   # 1000 in x $4/M + 200 out x $20/M

        # Owner approves by hand (no WhatsApp API yet): message is marked sent and recorded.
        r = self.client.post('/app/orders/%d/action/' % order.pk, {'action': 'approve', 'message_id': held.pk})
        self.assertTrue(r['Location'].startswith('https://wa.me/8801711000000?text=How%20many'))
        # Owner answers the price question in the app (the assistant's follow-up is run below instead
        # of in the background).
        with mock.patch('documents.app_views.run_assistant_later') as later:
            self.client.post('/app/orders/%d/action/' % order.pk, {'action': 'answer', 'answer': '250', 'question_seq': 1})
        later.assert_called_once()

        fake2 = FakeClaude([
            [('update_items', {'changes': [{'action': 'update', 'item_number': 2, 'description': None, 'quantity': 10,
                                            'unit': None, 'price': 250, 'price_is_final': None, 'source': 'staff + client'}]})],
            [('show_bill', {})], [],
        ])
        order = self.incoming('10 bulbs', fake2)
        self.assertEqual(order.status, 'SENT')
        self.assertEqual(order.bill_version, 1)
        bill_msg = OutgoingMessage.objects.filter(order=order, text__contains='code %s' % order.version_code).get()
        self.assertIn('Bill version 1', bill_msg.text)

        # Confirmation before the bill was actually sent is refused.
        fake3 = FakeClaude([[('record_confirmation', {'version': 1, 'client_words': 'ok'})], []])
        order = self.incoming('ok', fake3)
        self.assertEqual(order.status, 'SENT')
        result = json.loads(fake3.requests[1]['messages'][-1]['content'][0]['content'])
        self.assertIn('not been sent', result['error'])

        # Sent, then the client says OK: confirmation is recorded against version 1.
        self.client.post('/app/orders/%d/action/' % order.pk, {'action': 'approve', 'message_id': bill_msg.pk})
        fake4 = FakeClaude([[('record_confirmation', {'version': 1, 'client_words': 'ok thik ache'})],
                            [('send_documents', {'documents': ['invoice', 'challan']})], []])
        order = self.incoming('ok thik ache', fake4)
        self.assertEqual(order.status, 'CONFIRMED')
        self.assertEqual(order.transaction.status, 'APPROVED')
        confirmed = order.events.get(kind='client_confirmed')
        self.assertEqual((confirmed.data['version'], confirmed.data['code']), (1, order.version_code))
        self.assertEqual(OutgoingMessage.objects.filter(order=order, document__in=['invoice', 'challan']).count(), 2)

        ok, verdict = ledger.verify(order)
        self.assertTrue(ok, verdict)
        page = self.client.get('/app/orders/%d/record/' % order.pk)
        self.assertContains(page, 'Record verified')
        self.assertContains(page, 'Client confirmed bill version 1')

    def test_wrong_version_is_refused(self):
        order = ClientOrder.objects.create(key='X', title='X', bill_version=3, version_code='ABC', status='SENT')
        ledger.record(order, 'bill_shown', 'system', 'v3', {'version': 3})
        out = order_agent._tool_record_confirmation(order, {'version': 2, 'client_words': 'ok'})
        self.assertIn('version 3', out['error'])

    def test_handover_when_ai_declines(self):
        fake = FakeClaude([])
        fake.create = lambda **p: _resp(stop='refusal')
        fake.beta = SimpleNamespace(messages=SimpleNamespace(create=fake.create))
        order = self.incoming('Rice 5 kg', fake)
        self.assertTrue(order.paused)
        self.assertTrue(order.events.filter(kind='handoff').exists())


class LedgerTest(TestCase):
    def test_tampering_is_detected(self):
        order = ClientOrder.objects.create(key='K', title='K')
        for i in range(3):
            ledger.record(order, 'message_in', 'client', 'message %d' % i)
        self.assertEqual(ledger.verify(order)[0], True)
        with self.assertRaises(RuntimeError):
            e = order.events.get(seq=2)
            e.summary = 'changed'
            e.save()
        with self.assertRaises(RuntimeError):
            order.events.get(seq=2).delete()
        # Even a direct database edit is caught.
        with connection.cursor() as cur:
            cur.execute("UPDATE documents_orderevent SET summary='Rice 50 kg' WHERE seq=2")
        ok, verdict = ledger.verify(order)
        self.assertFalse(ok)
        self.assertIn('Entry 2', verdict)


@override_settings(STORAGES=STATIC)
class RuleFlowRecordTest(TestCase):
    """Without any API keys, the one-tap flow is recorded too."""

    def test_one_tap_flow_is_recorded(self):
        from .inbox_views import run_order_engine
        owner = User.objects.create_user('owner', password='pw', role='OWNER')
        self.client.force_login(owner)
        cat = TransactionCategory.objects.create(name='Grocery Item', invoice_prefix='GRO')
        Product.objects.create(name='Miniket Rice', category=cat, unit='Kg', base_price=58, upscale_value=64)
        BusinessParty.objects.create(party_type='BUYER', name='Rahim Store', whatsapp_number='8801711000000')
        store_message('notification', 'Miniket rice 25 kg', 'Rahim Store')
        run_order_engine()
        order = ClientOrder.objects.get()
        self.client.post('/app/orders/%d/action/' % order.pk, {'action': 'send_check'})
        store_message('notification', 'ok', 'Rahim Store')
        run_order_engine()
        kinds = list(order.events.values_list('kind', flat=True))
        for kind in ('message_in', 'change', 'bill_shown', 'message_out', 'client_confirmed'):
            self.assertIn(kind, kinds)
        self.assertTrue(ledger.verify(order)[0])


@override_settings(WHATSAPP_TOKEN='tok', WHATSAPP_PHONE_NUMBER_ID='123', WHATSAPP_APP_SECRET='secret',
                   WHATSAPP_VERIFY_TOKEN='verify-me', ANTHROPIC_API_KEY='PASTE-YOUR-ANTHROPIC-API-KEY-HERE')
class WhatsAppWebhookTest(TestCase):
    def post(self, payload, secret='secret'):
        body = json.dumps(payload).encode()
        sig = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return self.client.post('/api/whatsapp/webhook/', body, content_type='application/json',
                                HTTP_X_HUB_SIGNATURE_256=sig)

    def test_verification(self):
        ok = self.client.get('/api/whatsapp/webhook/', {'hub.mode': 'subscribe', 'hub.verify_token': 'verify-me',
                                                        'hub.challenge': '42'})
        self.assertEqual(ok.content, b'42')
        self.assertEqual(self.client.get('/api/whatsapp/webhook/', {'hub.mode': 'subscribe',
                                                                    'hub.verify_token': 'x'}).status_code, 403)

    def test_messages_and_receipts(self):
        payload = {'entry': [{'changes': [{'value': {
            'contacts': [{'wa_id': '8801711000000', 'profile': {'name': 'Rahim'}}],
            'messages': [{'from': '8801711000000', 'id': 'wamid.A1', 'timestamp': '1763900000',
                          'type': 'text', 'text': {'body': 'Rice 5 kg'}}]}}]}]}
        self.assertEqual(self.post(payload, secret='wrong').status_code, 403)
        with mock.patch('threading.Thread'):
            self.assertEqual(self.post(payload).status_code, 200)
            self.post(payload)                                   # Meta retries: stored once
        msg = InboxMessage.objects.get()
        self.assertEqual((msg.sender, msg.phone, msg.wa_id, msg.text), ('Rahim', '8801711000000', 'wamid.A1', 'Rice 5 kg'))

        order = ClientOrder.objects.create(key='Rahim', title='Rahim', phone='8801711000000')
        out = OutgoingMessage.objects.create(order=order, to_phone='8801711000000', text='hi', status='SENT', wa_id='wamid.OUT')
        with mock.patch('threading.Thread'):
            self.post({'entry': [{'changes': [{'value': {'statuses': [
                {'id': 'wamid.OUT', 'status': 'read', 'timestamp': '1763900100'}]}}]}]})
        self.assertIn('read by the client', order.events.get().summary)

    def test_sending_uses_the_api(self):
        order = ClientOrder.objects.create(key='R', title='R', phone='8801711000000')
        store_message('notification', 'hello', 'R')                       # client wrote recently
        InboxMessage.objects.update(phone='8801711000000')
        fake = mock.Mock(status_code=200, content=b'1', json=lambda: {'messages': [{'id': 'wamid.S1'}]})
        with mock.patch.object(whatsapp, '_request', return_value=fake) as req:
            msg = whatsapp.queue(order, 'Hello', 'assistant', '')
        self.assertEqual((msg.status, msg.wa_id), ('SENT', 'wamid.S1'))
        self.assertEqual(req.call_args[1]['json']['to'], '8801711000000')
        self.assertEqual(order.events.get().kind, 'message_out')

    @override_settings(WHATSAPP_REOPEN_TEMPLATE='order_update')
    def test_after_24_hours_the_template_goes_first(self):
        order = ClientOrder.objects.create(key='R', title='Rahim', phone='8801711000000')
        sent = []

        def fake_request(method, url, **kw):
            sent.append(kw.get('json'))
            return mock.Mock(status_code=200, content=b'1', json=lambda: {'messages': [{'id': 'wamid.%d' % len(sent)}]})
        with mock.patch.object(whatsapp, '_request', side_effect=fake_request):
            msg = whatsapp.queue(order, 'Your bill is ready', 'assistant', '')
            self.assertEqual(sent[0]['type'], 'template')
            self.assertEqual(sent[0]['template']['name'], 'order_update')
            msg.refresh_from_db()
            self.assertEqual((msg.status, msg.channel), ('PENDING', 'await_reply'))
            # The client replies: the held message goes out.
            with mock.patch('threading.Thread'):
                self.post({'entry': [{'changes': [{'value': {'messages': [
                    {'from': '8801711000000', 'id': 'wamid.IN', 'timestamp': '1763900000', 'type': 'text',
                     'text': {'body': 'yes?'}}]}}]}]})
        InboxMessage.objects.filter(wa_id='wamid.IN').update(received_at=__import__('django.utils.timezone', fromlist=['now']).now())
        with mock.patch.object(whatsapp, '_request', side_effect=fake_request):
            whatsapp.release_waiting('8801711000000')
        msg.refresh_from_db()
        self.assertEqual(msg.status, 'SENT')
        self.assertEqual(sent[-1]['text']['body'], 'Your bill is ready')

    def test_staff_reply_from_whatsapp_pauses_the_assistant(self):
        order = ClientOrder.objects.create(key='R', title='Rahim', phone='8801711000000')
        with mock.patch('threading.Thread'):
            self.post({'entry': [{'changes': [{'value': {'message_echoes': [
                {'from': '8801900000000', 'to': '8801711000000', 'id': 'wamid.E1', 'timestamp': '1763900000',
                 'type': 'text', 'text': {'body': 'I will call you'}}]}}]}]})
        order.refresh_from_db()
        self.assertTrue(order.paused)
        self.assertIn('WhatsApp app', order.events.filter(kind='message_out').get().summary)


class SwitchTest(TestCase):
    def test_assistant_off_uses_the_rule_reader(self):
        from .inbox_views import run_order_engine
        from .models import AppSetting
        AppSetting.put('assistant_on', False)
        with override_settings(ANTHROPIC_API_KEY='sk-ant-test'), \
                mock.patch.object(order_agent, 'handle_new_messages') as agent, \
                mock.patch('documents.order_engine.process_new_messages') as rules:
            run_order_engine()
        agent.assert_not_called()
        rules.assert_called_once()

    @override_settings(STORAGES=STATIC, ANTHROPIC_API_KEY='sk-ant-test', WHATSAPP_TOKEN='tok',
                       WHATSAPP_PHONE_NUMBER_ID='123')
    def test_documents_are_sent_as_pdfs_and_fingerprinted(self):
        from . import pdf_render
        from .models import AppSetting
        AppSetting.put('autopilot_on', True)
        AppSetting.put('autopilot_trusted_after', 0)
        cat = TransactionCategory.objects.create(name='G', invoice_prefix='G')
        order = ClientOrder.objects.create(key='R', title='R', phone='8801711000000', status='CONFIRMED',
                                           buyer_name='R', category=cat,
                                           items=[{'description': 'Rice', 'quantity': 1, 'unit': 'Kg', 'price': 10}])
        from . import order_engine
        order_engine._save_bill(order)
        order.save()
        store_message('notification', 'ok', 'R')
        InboxMessage.objects.update(phone='8801711000000')
        pdf = b'%PDF-1.4 test invoice'
        ok = mock.Mock(status_code=200, content=b'1', json=lambda: {'id': 'media1', 'messages': [{'id': 'wamid.D'}]})
        with mock.patch.object(pdf_render, 'render', return_value=('Invoice_G-1.pdf', pdf)), \
                mock.patch.object(whatsapp, '_request', return_value=ok) as req:
            out = order_agent._tool_send_documents(order, {'documents': ['invoice']})
        self.assertEqual(out['documents']['invoice'], 'sent')
        self.assertEqual(req.call_args[1]['json']['type'], 'document')
        sent = order.events.get(kind='document_sent')
        self.assertEqual(sent.data['file_sha256'], hashlib.sha256(pdf).hexdigest())
        order.refresh_from_db()
        self.assertEqual(order.status, 'DELIVERED')

    @override_settings(STORAGES=STATIC)
    def test_setup_switches(self):
        from . import app_settings
        owner = User.objects.create_user('owner', password='pw', role='OWNER')
        self.client.force_login(owner)
        self.client.post('/app/setup/', {'autopilot_on': 'on', 'max_total': '20000', 'trusted_after': '1',
                                         'quiet_start': '22', 'quiet_end': '8'})
        self.assertFalse(app_settings.assistant_on())          # unticked = off, and stays off
        self.assertTrue(app_settings.autopilot_on())
        self.assertEqual((app_settings.max_total(), app_settings.trusted_after(), app_settings.quiet_hours()),
                         (20000.0, 1, None))


@override_settings(STORAGES=STATIC)
class CostTest(TestCase):
    def test_prices(self):
        self.assertEqual(ai_client.cost_of('claude-opus-5-5', 1_000_000, 1_000_000), Decimal('24'))
        self.assertEqual(ai_client.cost_of('claude-opus-5-5', cache_read=1_000_000, cache_write=1_000_000), Decimal('5.2'))

    @override_settings(ANTHROPIC_API_KEY='PASTE-YOUR-ANTHROPIC-API-KEY-HERE')
    def test_placeholder_key_is_not_configured(self):
        self.assertFalse(ai_client.is_configured())
        with self.assertRaises(ai_client.NotConfigured):
            ai_client.client()

    def test_costs_page(self):
        owner = User.objects.create_user('owner', password='pw', role='OWNER')
        self.client.force_login(owner)
        AIUsage.objects.create(purpose='order_assistant', model='claude-opus-5-5', input_tokens=1000,
                               output_tokens=200, cost_usd=Decimal('0.008'))
        page = self.client.get('/app/ai-costs/')
        self.assertContains(page, '0.0080')
        self.assertContains(self.client.get('/app/setup/'), 'placeholder')
