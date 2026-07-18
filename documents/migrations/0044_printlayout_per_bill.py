import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0043_printlayout'),
    ]

    operations = [
        migrations.AddField(
            model_name='printlayout',
            name='transaction',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='print_layouts',
                to='documents.transaction',
            ),
        ),
        migrations.AlterField(
            model_name='printlayout',
            name='doc_type',
            field=models.CharField(
                choices=[('invoice', 'Invoice'), ('quotation', 'Quotation'),
                         ('challan', 'Challan'), ('mushok', 'Mushok')],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='printlayout',
            name='data',
            field=models.JSONField(default=dict, help_text='Saved layout store for this bill + document type'),
        ),
        migrations.AlterUniqueTogether(
            name='printlayout',
            unique_together={('transaction', 'doc_type')},
        ),
    ]
