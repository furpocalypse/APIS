# -*- coding: utf-8 -*-
# Generated by Django 1.9.8 on 2016-12-23 22:32


from django.db import migrations, models

import registration.models


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0031_auto_20161223_1717"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attendee",
            name="registrationToken",
            field=models.CharField(
                default=registration.models.getRegistrationToken, max_length=200
            ),
        ),
        migrations.AlterField(
            model_name="dealer",
            name="registrationToken",
            field=models.CharField(
                default=registration.models.getRegistrationToken, max_length=200
            ),
        ),
        migrations.AlterField(
            model_name="staff",
            name="registrationToken",
            field=models.CharField(
                default=registration.models.getRegistrationToken, max_length=200
            ),
        ),
    ]
