# Generated by Django 3.2.9 on 2022-05-04 09:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nodes', '0007_update_modified_at_automatically'),
    ]

    operations = [
        migrations.AddField(
            model_name='nodeconfig',
            name='order',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='Order'),
        ),
    ]
