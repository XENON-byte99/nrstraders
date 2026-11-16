import json
import os
import tempfile
from decimal import Decimal

from django.test import TestCase, override_settings

from accounts.models import User
from .models import BusinessParty, InboxMessage, Product, Transaction, TransactionCategory
from .text_import import extract_items, parse_chat_export
from . import inbox_views


class ExtractItemsTest(TestCase):
    def one(self, line):
        items, _ = extract_items(line)
        self.assertEqual(len(items), 1, items)
        return items[0]

    def test_quantity_unit_and_price(self):
        item = self.one('5 kg rice @ 60')
        self.assertEqual((item['description'], item['quantity'], item['unit'], item['unit_price']),
                         ('rice', 5.0, 'Kg', 60.0))

    def test_trailing_quantity_and_currency(self):
        item = self.one('08 mm Nut supply - 2 pcs 470 tk')
        self.assertEqual((item['description'], item['quantity'], item['unit_price']),
                         ('08 mm Nut supply', 2.0, 470.0))

    def test_dimension_is_part_of_the_name(self):
        item = self.one('2 pcs 1 inch Cable Clip')
        self.assertEqual((item['description'], item['quantity']), ('1 inch Cable Clip', 2.0))

    def test_line_total_becomes_unit_price(self):
        item = self.one('Oil 2 ltr = 360')
        self.assertEqual((item['quantity'], item['unit'], item['unit_price']), (2.0, 'Ltr', 180.0))

    def test_thousands_separator(self):
        self.assertEqual(self.one('Ceiling fan 2 pcs 3,200/-')['unit_price'], 3200.0)

    def test_comma_separated_items(self):
        items, _ = extract_items('rice 5kg, dal 2kg, oil 1 ltr')
        self.assertEqual([i['description'] for i in items], ['rice', 'dal', 'oil'])

    def test_bengali_digits_and_units(self):
        item = self.one('চাল ৫ কেজি ৬০ টাকা')
        self.assertEqual((item['description'], item['quantity'], item['unit'], item['unit_price']),
                         ('চাল', 5.0, 'Kg', 60.0))

    def test_numbered_list_and_strict_format(self):
        items, _ = extract_items('1. Chair 5\n2) Table - 2\nDesk | 3 | 900')
        self.assertEqual([(i['description'], float(i['quantity'])) for i in items],
                         [('Chair', 5.0), ('Table', 2.0), ('Desk', 3.0)])

    def test_greetings_are_skipped(self):
        items, skipped = extract_items('Assalamu alaikum bhai\nRice 5 kg\nThanks')
        self.assertEqual(len(items), 1)
        self.assertEqual(len(skipped), 2)


class ChatExportTest(TestCase):
    EXPORT = (
        '12/05/24, 10:30 PM - Messages and calls are end-to-end encrypted.\n'
        '12/05/24, 10:30 PM - Rahim Store: 5 kg rice\n2 ltr oil\n'
        '[13/05/24, 9:05:11 AM] Karim: <Media omitted>\n'
        '[13/05/24, 9:06:00 AM] Karim: Cable clip 12 pcs\n'
    )

    def test_android_and_iphone_formats(self):
        msgs = parse_chat_export(self.EXPORT)
        self.assertEqual([(m['sender'], m['text']) for m in msgs],
                         [('Rahim Store', '5 kg rice\n2 ltr oil'), ('Karim', 'Cable clip 12 pcs')])
        self.assertEqual((msgs[0]['when'].day, msgs[0]['when'].month, msgs[0]['when'].hour), (12, 5, 22))

    def test_plain_text_is_not_an_export(self):
        self.assertEqual(parse_chat_export('Rice 5 kg\nOil 2 ltr'), [])


class InboxFlowTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('owner', password='pw', role='OWNER')
        self.client.force_login(self.user)
        self.category = TransactionCategory.objects.create(name='Grocery Item', invoice_prefix='GRO')
        Product.objects.create(name='Miniket Rice', category=self.category, unit='Kg',
                               base_price=Decimal('55'), upscale_value=Decimal('62'))
        self.party = BusinessParty.objects.create(party_type='BUYER', name='Rahim Store',
                                                  whatsapp_number='8801711000000')

    def test_import_export_deduplicates(self):
        for _ in range(2):
            self.client.post('/messages/import/', {'text': ChatExportTest.EXPORT})
        self.assertEqual(InboxMessage.objects.count(), 2)

    def test_smart_parse_fills_price_from_products(self):
        r = self.client.post('/api/quick-bill/smart-parse/', json.dumps({
            'text': 'Grocery Item\nHello bhai\nMiniket rice 5 kg\nNew thing 2 pcs\nplease send today'}),
            content_type='application/json')
        data = r.json()
        self.assertTrue(data['ok'], data)
        self.assertEqual(data['category']['id'], self.category.id)
        self.assertEqual(len(data['rows']), 2)
        self.assertEqual(data['rows'][0]['unit_price'], 62.0)
        self.assertEqual(data['rows'][1]['status'], 'needs_price')

    def test_sender_matches_contact_by_number(self):
        self.assertEqual(inbox_views.match_party('+880 1711-000000'), self.party)
        self.assertEqual(inbox_views.match_party('rahim store'), self.party)
        self.assertIsNone(inbox_views.match_party('Nobody'))

    def test_bill_form_prefill_and_mark_billed(self):
        msg, _ = inbox_views.store_message('paste', 'Miniket rice 5 kg', 'Rahim Store')
        page = self.client.get('/bills/new/?inbox=%d' % msg.pk)
        self.assertContains(page, 'name="inbox_ids" value="%d"' % msg.pk)
        self.assertContains(page, 'Matched saved contact: Rahim Store')
        bill = Transaction.objects.create(transaction_category=self.category, invoice_number='GRO-1',
                                          supplier_name='S', buyer_name='B')
        inbox_views.mark_messages_billed(str(msg.pk), bill)
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.transaction), ('BILLED', bill))

    def test_spool_is_ingested_once(self):
        with tempfile.TemporaryDirectory() as spool, override_settings(INBOX_SPOOL_DIR=spool):
            batch = [{'source': 'notification', 'app': 'WhatsApp', 'sender': 'Karim',
                      'text': 'Rice 5 kg', 'ts': 1731650000000}]
            for name in ('a.json', 'b.json'):          # WhatsApp re-posts the same message
                with open(os.path.join(spool, name), 'w') as fh:
                    json.dump(batch, fh)
            r = self.client.get('/api/inbox/status/')
            self.assertEqual(r.json(), {'new': 1, 'added': 1})
            self.assertEqual(os.listdir(spool), [])

    def test_inbox_page_and_actions(self):
        msg, _ = inbox_views.store_message('paste', 'Rice 5 kg', 'Karim')
        self.assertContains(self.client.get('/messages/'), 'Rice 5 kg')
        r = self.client.post('/messages/action/', {'ids': [msg.pk], 'action': 'bill'})
        self.assertEqual(r['Location'], '/bills/new/?inbox=%d' % msg.pk)
        self.client.post('/messages/action/', {'ids': [msg.pk], 'action': 'archive'})
        msg.refresh_from_db()
        self.assertEqual(msg.status, 'ARCHIVED')


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class AppScreensTest(TestCase):
    """The phone app's own screens and its bill composer."""

    def setUp(self):
        self.user = User.objects.create_user('owner', password='pw', role='OWNER')
        self.client.force_login(self.user)
        self.buyer = BusinessParty.objects.create(party_type='BUYER', name='Rahim Store')
        self.supplier = BusinessParty.objects.create(party_type='SUPPLIER', name='NRS Traders')
        self.category = TransactionCategory.objects.create(
            name='Grocery Item', invoice_prefix='GRO', default_vat=Decimal('15'), default_duty=Decimal('5'),
            default_supplier=self.supplier)

    def save(self, **extra):
        payload = {'category_id': self.category.id, 'buyer_id': self.buyer.id, 'mode': 'forward', 'items': [
            {'description': 'Rice', 'quantity': 5, 'unit': 'Kg', 'price': 60},
            {'description': 'Oil', 'quantity': 2, 'unit': 'Ltr', 'price': 180}]}
        payload.update(extra)
        return self.client.post('/app/api/bill/', json.dumps(payload), content_type='application/json').json()

    def test_screens_render(self):
        self.save()
        for path in ('/app/', '/app/bills/', '/app/bills/new/', '/app/inbox/', '/app/products/',
                     '/app/contacts/', '/app/more/', '/app/bills/%d/' % Transaction.objects.get().pk):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_forward_bill_uses_category_rules(self):
        data = self.save()
        bill = Transaction.objects.get(pk=data['id'])
        self.assertEqual((bill.status, bill.buyer_name, bill.supplier_name), ('DRAFT', 'Rahim Store', 'NRS Traders'))
        self.assertEqual((bill.vat_percentage, bill.duty_percentage), (Decimal('15'), Decimal('5')))
        self.assertTrue(bill.invoice_number.startswith('GRO-'))
        self.assertEqual(bill.display_subtotal, Decimal('660'))
        self.assertGreater(bill.grand_total, Decimal('660'))
        self.assertTrue(Product.objects.filter(name='Rice').exists())

    def test_reverse_bill_totals_what_was_typed(self):
        bill = Transaction.objects.get(pk=self.save(mode='reverse')['id'])
        self.assertEqual(bill.calculation_mode, 'reverse')
        self.assertAlmostEqual(float(bill.grand_total), 660.0, delta=0.02)

    def test_preview_saves_nothing(self):
        data = self.save(preview=True)
        self.assertTrue(data['ok'])
        self.assertGreater(data['totals']['grand_total'], 660)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertFalse(Product.objects.exists())

    def test_inbox_messages_are_filed_under_the_bill(self):
        msg, _ = inbox_views.store_message('paste', 'Rice 5 kg', 'Rahim Store')
        bill_id = self.save(inbox_ids=str(msg.pk))['id']
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.transaction_id), ('BILLED', bill_id))

    def test_validation(self):
        self.assertIn('category', self.save(category_id=None)['error'].lower())
        self.assertIn('item', self.save(items=[])['error'].lower())


class LiveSyncServerTest(TestCase):
    """The live server's side of phone sync."""

    def setUp(self):
        from . import live_sync
        self.live_sync = live_sync
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = User.objects.create_user('owner', password='pw', role='OWNER')
        self.category = TransactionCategory.objects.create(name='Grocery Item', invoice_prefix='GRO')
        r = self.client.post('/api/sync/login/', json.dumps({'username': 'owner', 'password': 'pw'}),
                             content_type='application/json')
        self.auth = {'HTTP_AUTHORIZATION': 'Token ' + r.json()['token']}

    def exchange(self, **payload):
        with override_settings(SYNC_BACKUP_DIR=self.tmp.name):
            return self.client.post('/api/sync/exchange/', json.dumps(payload),
                                    content_type='application/json', **self.auth).json()

    def record(self, **fields):
        data = {'transaction_category': {'uuid': str(self.category.uuid)}, 'buyer_name': 'B', 'status': 'DRAFT'}
        data.update(fields)
        return data

    def test_login_is_owner_only_and_token_is_required(self):
        User.objects.create_user('clerk', password='pw', role='BUYER')
        r = self.client.post('/api/sync/login/', json.dumps({'username': 'clerk', 'password': 'pw'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 403)
        r = self.client.post('/api/sync/exchange/', '{}', content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_every_server_change_gets_a_new_number(self):
        first = self.category.sync_rev
        self.category.name = 'Groceries'
        self.category.save()
        self.assertGreater(self.category.sync_rev, first)
        changes = self.exchange(since=first)['changes']
        self.assertEqual([c['fields']['name'] for c in changes], ['Groceries'])

    def test_phone_record_is_created_then_conflicts_when_stale(self):
        uid = '11111111-1111-1111-1111-111111111111'
        reply = self.exchange(since=0, changes=[{'model': 'Transaction', 'uuid': uid, 'rev': 0,
                                                 'fields': self.record(invoice_number='GRO-1')}])
        self.assertEqual(reply['results'][0]['status'], 'ok')
        bill = Transaction.objects.get(uuid=uid)
        bill.buyer_name = 'Changed on website'
        bill.save()
        stale = self.exchange(since=0, changes=[{'model': 'Transaction', 'uuid': uid, 'rev': reply['results'][0]['rev'],
                                                 'fields': self.record(buyer_name='Changed on phone')}])
        self.assertEqual(stale['results'][0]['status'], 'conflict')
        self.assertEqual(Transaction.objects.get(uuid=uid).buyer_name, 'Changed on website')

    def test_clashing_invoice_number_is_renumbered(self):
        Transaction.objects.create(transaction_category=self.category, invoice_number='GRO-202601-0001')
        uid = '22222222-2222-2222-2222-222222222222'
        self.exchange(changes=[{'model': 'Transaction', 'uuid': uid, 'rev': 0,
                                'fields': self.record(invoice_number='GRO-202601-0001')}])
        self.assertNotEqual(Transaction.objects.get(uuid=uid).invoice_number, 'GRO-202601-0001')

    def test_deletes_travel_both_ways(self):
        bill = Transaction.objects.create(transaction_category=self.category, invoice_number='X')
        uid, rev = str(bill.uuid), bill.sync_rev
        bill.delete()
        self.assertIn({'model': 'Transaction', 'uuid': uid}, self.exchange(since=rev)['deletes'])
        other = Transaction.objects.create(transaction_category=self.category, invoice_number='Y')
        self.exchange(deletes=[{'model': 'Transaction', 'uuid': str(other.uuid)}])
        self.assertFalse(Transaction.objects.filter(pk=other.pk).exists())

    def test_backup_is_made_before_phone_changes_are_applied(self):
        from django.db import connection
        if connection.is_in_memory_db():
            self.skipTest('needs a file database')
        self.exchange(changes=[{'model': 'Transaction', 'uuid': '33333333-3333-3333-3333-333333333333', 'rev': 0,
                                'fields': self.record()}])
        self.assertTrue(any(f.endswith('before-sync.sqlite3') for f in os.listdir(self.tmp.name)))
