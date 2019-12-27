# -*- coding: utf-8 -*-
# Generated by Django 1.9.8 on 2016-09-26 23:25
from __future__ import unicode_literals

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0013_auto_20160917_1045"),
    ]

    operations = [
        migrations.CreateModel(
            name="TableSize",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=200)),
            ],
            options={"abstract": False,},
        ),
        migrations.AddField(
            model_name="dealer",
            name="approved",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="dealer",
            name="attendee",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="registration.Attendee",
            ),
        ),
        migrations.AddField(
            model_name="dealer",
            name="businessName",
            field=models.CharField(default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="dealer",
            name="description",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="dealer",
            name="farFrom",
            field=models.CharField(default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="dealer",
            name="license",
            field=models.CharField(default="", max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="dealer",
            name="nearTo",
            field=models.CharField(default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="dealer",
            name="needPower",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="dealer",
            name="needWifi",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="dealer", name="notes", field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="dealer",
            name="registrationToken",
            field=models.CharField(default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="dealer",
            name="tableNumber",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="dealer",
            name="wallSpace",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="dealer",
            name="website",
            field=models.CharField(default="", max_length=500),
            preserve_default=False,
        ),
    ]
