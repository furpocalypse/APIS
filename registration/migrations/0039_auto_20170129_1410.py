# -*- coding: utf-8 -*-
# Generated by Django 1.9.8 on 2017-01-29 19:10


from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0038_auto_20170128_1001"),
    ]

    operations = [
        # Removing columns isn't possible with sqlite backend:
        # https://github.com/miguelgrinberg/Flask-Migrate/issues/17
        # migrations.RemoveField(
        #    model_name='staffjersey',
        #    name='jersey_ptr',
        # ),
        migrations.DeleteModel(
            name="StaffJersey",
        ),
    ]
