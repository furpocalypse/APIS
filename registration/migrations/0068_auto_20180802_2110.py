# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2018-08-03 01:10
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0067_auto_20180802_2106"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cashdrawer",
            name="tendered",
            field=models.DecimalField(decimal_places=2, max_digits=8, null=True),
        ),
    ]
