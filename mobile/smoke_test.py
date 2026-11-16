#!/usr/bin/env python3
"""End-to-end check of a running offline build (computer or phone).

    python mobile/smoke_test.py http://127.0.0.1:8765 setup    # fresh install -> first bill
    python mobile/smoke_test.py http://127.0.0.1:8765 inbox 2  # expect >= 2 new inbox messages

The CI emulator job runs this against the real app through `adb forward`.
Standard library only.
"""
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1].rstrip('/')
MODE = sys.argv[2] if len(sys.argv) > 2 else 'setup'
USER, PASSWORD = 'smoke', 'smoke-pass-1'

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def csrf():
    return next((c.value for c in jar if c.name == 'csrftoken'), '')


def get(path):
    with opener.open(BASE + path, timeout=60) as r:
        return r.status, r.url, r.read()


def post(path, fields=None, payload=None):
    if payload is not None:
        body, ctype = json.dumps(payload).encode(), 'application/json'
    else:
        body = urllib.parse.urlencode(dict(fields, csrfmiddlewaretoken=csrf())).encode()
        ctype = 'application/x-www-form-urlencoded'
    req = urllib.request.Request(BASE + path, data=body, headers={
        'Content-Type': ctype, 'X-CSRFToken': csrf(), 'Referer': BASE + path})
    try:
        with opener.open(req, timeout=120) as r:
            return r.status, r.url, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.url, e.read()


def check(label, ok, detail=''):
    print(('PASS  ' if ok else 'FAIL  ') + label + (('  ' + str(detail)[:300]) if detail and not ok else ''))
    if not ok:
        sys.exit(1)


def wait_for_server(seconds=300):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            get('/static/vendor/tailwind.css')
            return
        except Exception:
            time.sleep(3)
    check('server answers within %ds' % seconds, False)


def login():
    get('/login/')
    status, url, _ = post('/login/', {'username': USER, 'password': PASSWORD})
    check('sign in', status == 200 and '/login/' not in url, url)


started = time.time()
wait_for_server()
print('server answered after %.0fs' % (time.time() - started))

if MODE == 'wait':
    pass
elif MODE == 'setup':
    status, url, body = get('/')
    check('fresh install opens the setup screen', url.endswith('/setup/'), url)
    check('no CDN links left in the page', b'cdn.tailwindcss.com' not in body and b'googleapis' not in body
          and b'cdnjs' not in body)
    for asset in ('/static/vendor/tailwind.css', '/static/vendor/fonts/fonts.css',
                  '/static/vendor/fontawesome-6.4.0/css/all.min.css',
                  '/static/vendor/xlsx-0.18.5/xlsx.full.min.js', '/static/js/print_engine.js'):
        check('serves ' + asset, get(asset)[0] == 200)

    status, url, _ = post('/setup/', {'mode': 'create', 'full_name': 'Smoke Test', 'username': USER,
                                      'password': PASSWORD, 'password2': PASSWORD})
    check('owner account created', status == 200 and '/setup/' not in url and '/login/' not in url, url)

    status, url, body = post('/messages/import/', {'sender': 'Rahim Store', 'text':
                             '12/05/24, 10:30 PM - Rahim Store: Rice 5 kg @ 60\nOil 2 ltr = 360\n'
                             '12/05/24, 10:31 PM - Rahim Store: চাল ৩ কেজি ৭০ টাকা'})
    check('chat text imported into the inbox', status == 200 and b'Oil 2 ltr' in body, url)

    status, _, body = post('/api/quick-bill/smart-parse/', payload={'text': 'Salam bhai\nRice 5 kg @ 60\nOil 2 ltr = 360'})
    data = json.loads(body)
    rows = [(r['description'], r['quantity'], r['unit'], r['unit_price']) for r in data.get('rows', [])]
    check('message read into bill rows', rows == [('Rice', 5.0, 'Kg', 60.0), ('Oil', 2.0, 'Ltr', 180.0)], data)

    status, url, body = get('/bills/new/?inbox=1,2')
    check('bill form opens pre-filled from the inbox', b'name="inbox_ids" value="1,2"' in body, url)
    for path in ('/bills/', '/products/', '/contacts/', '/categories/', '/summary/', '/backup/', '/admin/'):
        check('page ' + path, get(path)[0] == 200)
    status, _, body = get('/backup/download/')
    check('backup downloads as a SQLite file', body.startswith(b'SQLite format 3'), body[:20])

elif MODE == 'inbox':
    want = int(sys.argv[3])
    login()
    count = 0
    for _ in range(20):
        count = json.loads(get('/api/inbox/status/')[2])['new']
        if count >= want:
            break
        time.sleep(2)
    check('inbox holds at least %d new messages (has %d)' % (want, count), count >= want)
    body = get('/messages/')[2].decode('utf-8', 'replace')
    for needle in sys.argv[4:]:
        check('inbox shows "%s"' % needle, needle in body)

print('ALL GOOD')
