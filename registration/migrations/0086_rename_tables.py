# -*- coding: utf-8 -*-
# Generated by Django 1.11.17 on 2020-02-17 07:15


from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0085_auto_20200215_1029_squashed_0086_auto_20200215_1051"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="attendeeoptions",
            table="registration_attendee_options",
        ),
        migrations.AlterModelTable(
            name="banlist",
            table="registration_ban_list",
        ),
        migrations.AlterModelTable(
            name="dealerasst",
            table="registration_dealer_asst",
        ),
        migrations.AlterModelTable(
            name="orderitem",
            table="registration_order_item",
        ),
        migrations.AlterModelTable(
            name="pricelevel",
            table="registration_price_level",
        ),
        migrations.AlterModelTable(
            name="priceleveloption",
            table="registration_price_level_option",
        ),
        migrations.AlterModelTable(
            name="reservedbadgenumbers",
            table="registration_reserved_badge_numbers",
        ),
        migrations.AlterModelTable(
            name="shirtsizes",
            table="registration_shirt_sizes",
        ),
        migrations.AlterModelTable(
            name="tablesize",
            table="registration_table_size",
        ),
        migrations.AlterModelTable(
            name="temptoken",
            table="registration_temp_token",
        ),
    ]
