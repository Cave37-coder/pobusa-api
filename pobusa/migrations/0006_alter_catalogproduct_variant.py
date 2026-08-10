from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pobusa', '0005_game_catalogproduct_tcgcsvsource_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='catalogproduct',
            name='variant',
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
