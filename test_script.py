from django.test import Client
from django.contrib.auth import get_user_model
from documents.models import Transaction

User = get_user_model()
u = User.objects.filter(is_superuser=True).first()
c = Client(SERVER_NAME='127.0.0.1')
c.force_login(u)

err = 0
for t in Transaction.objects.all():
    if t.status != 'APPROVED':
        continue
    for view in ['quotation', 'invoice']:
        try:
            r = c.get(f'/bills/{t.id}/print/{view}/')
            if r.status_code != 200:
                print(f'Error {r.status_code} on {t.id} {view}')
                err += 1
        except Exception as e:
            print(f'Exception on {t.id} {view}: {e}')
            err += 1
if err == 0:
    print('All passed')
