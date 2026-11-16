import uuid

from django.db import migrations

MODELS = ['BusinessParty', 'TransactionCategory', 'Product', 'Transaction', 'TransactionItem',
          'VisualRequisition', 'CheckAuthorization']


def give_every_record_its_own_uuid(apps, schema_editor):
    """Sync matches records by uuid, so each must be unique.

    When the uuid column was first added, rows that already existed all
    received the same generated value. Keep the first holder of each uuid and
    give every other row (and any row without one) a fresh uuid.
    """
    for name in MODELS:
        model = apps.get_model('documents', name)
        seen = set()
        for pk, value in model.objects.order_by('pk').values_list('pk', 'uuid'):
            if value is None or value in seen:
                value = uuid.uuid4()
                model.objects.filter(pk=pk).update(uuid=value)
            seen.add(value)


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0047_live_sync'),
    ]

    operations = [
        migrations.RunPython(give_every_record_its_own_uuid, migrations.RunPython.noop),
    ]
