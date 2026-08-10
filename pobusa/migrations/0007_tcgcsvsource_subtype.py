from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pobusa', '0006_alter_catalogproduct_variant'),
    ]

    operations = [
        migrations.AddField(
            model_name='tcgcsvsource',
            name='tcgcsv_subtype_name',
            field=models.CharField(blank=True, default='Normal', max_length=100),
        ),
        migrations.AlterField(
            model_name='tcgcsvsource',
            name='tcgcsv_product_id',
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterUniqueTogether(
            name='tcgcsvsource',
            unique_together={('tcgcsv_product_id', 'tcgcsv_subtype_name')},
        ),
    ]
