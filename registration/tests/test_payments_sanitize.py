"""Locking tests for SECURITY_REVIEW MED-8 (Decision #12b).

sanitize_api_data must strip customer PII from Square/PayPal responses
while preserving the operational structure the payment lifecycle reads.
No DB → runs locally.
"""

import json

from django.test import SimpleTestCase

from registration.payments_sanitize import sanitize_api_data


class TestSanitizeApiData(SimpleTestCase):
    def test_none_passthrough(self):
        self.assertIsNone(sanitize_api_data(None))

    def test_unparseable_string_redacted(self):
        self.assertEqual(
            sanitize_api_data("<html>raw provider error for jane@x.com</html>"),
            "[redacted: unparseable provider body]",
        )

    def test_json_string_roundtrips_as_string(self):
        out = sanitize_api_data(json.dumps({"id": "P1", "email_address": "j@x.com"}))
        self.assertIsInstance(out, str)
        self.assertEqual(json.loads(out), {"id": "P1", "email_address": "[redacted]"})

    def test_square_payment_pii_stripped_operational_kept(self):
        square = {
            "payment": {
                "id": "pay_123",
                "status": "COMPLETED",
                "amount_money": {"amount": 4500, "currency": "USD"},
                "card_details": {
                    "card": {
                        "last_4": "1111",
                        "card_brand": "VISA",
                        "cardholder_name": "Jane Doe",
                    }
                },
                "billing_address": {
                    "address_line_1": "1 Main St",
                    "postal_code": "90210",
                    "locality": "Beverly Hills",
                },
                "buyer_email_address": "jane@example.com",
            }
        }
        out = sanitize_api_data(square)
        pay = out["payment"]
        # Operational fields intact.
        self.assertEqual(pay["id"], "pay_123")
        self.assertEqual(pay["status"], "COMPLETED")
        self.assertEqual(pay["amount_money"]["amount"], 4500)
        self.assertEqual(pay["card_details"]["card"]["last_4"], "1111")
        self.assertEqual(pay["card_details"]["card"]["card_brand"], "VISA")
        # PII gone.
        self.assertEqual(pay["card_details"]["card"]["cardholder_name"], "[redacted]")
        self.assertEqual(pay["billing_address"], {})
        self.assertEqual(pay["buyer_email_address"], "[redacted]")

    def test_paypal_order_pii_stripped_structure_kept(self):
        paypal = {
            "id": "ORDER-1",
            "status": "COMPLETED",
            "payer": {
                "name": {"given_name": "Jane", "surname": "Doe"},
                "email_address": "jane@example.com",
            },
            "purchase_units": [
                {
                    "amount": {"value": "45.00", "currency_code": "USD"},
                    "shipping": {
                        "name": {"full_name": "Jane Doe"},
                        "address": {"address_line_1": "1 Main St"},
                    },
                    "payments": {
                        "captures": [{"id": "CAP-1", "status": "COMPLETED"}],
                        "refunds": [{"id": "REF-1", "status": "COMPLETED"}],
                    },
                }
            ],
        }
        out = sanitize_api_data(paypal)
        self.assertEqual(out["id"], "ORDER-1")
        self.assertEqual(out["status"], "COMPLETED")
        self.assertEqual(out["payer"], {})  # whole PII container dropped
        pu = out["purchase_units"][0]
        self.assertEqual(pu["amount"]["value"], "45.00")
        self.assertEqual(pu["shipping"], {})
        # Refund/capture ids+status survive (refund/dispute/refresh need these).
        self.assertEqual(pu["payments"]["captures"][0]["id"], "CAP-1")
        self.assertEqual(pu["payments"]["refunds"][0]["status"], "COMPLETED")

    def test_idempotent(self):
        once = sanitize_api_data({"payer": {"email_address": "a@b.c"}, "id": "X"})
        twice = sanitize_api_data(once)
        self.assertEqual(once, twice)

    def test_top_level_remains_dict_for_isinstance_checks(self):
        # Several readers do `isinstance(order.apiData, dict)` /
        # `order.apiData.get(...)` — the top level must stay a dict.
        out = sanitize_api_data({"payer": {"email_address": "a@b.c"}})
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("payer"), {})

    def test_operational_name_country_preserved_pii_still_removed(self):
        # Peer-review non-block-3 / ATTACK-5: operational `name` /
        # `country_code` OUTSIDE a PII container are preserved (line-item
        # SKU, card issuing country), while personal names inside the
        # collapsed PII containers are still removed.
        out = sanitize_api_data(
            {
                "purchase_units": [
                    {
                        "items": [{"name": "Sponsor Badge", "quantity": "1"}],
                        "shipping": {"name": {"full_name": "Jane Doe"}},
                    }
                ],
                "payment_source": {"card": {"country_code": "US", "last_digits": "1111"}},
                "payer": {"name": {"given_name": "Jane"}},
            }
        )
        pu = out["purchase_units"][0]
        self.assertEqual(pu["items"][0]["name"], "Sponsor Badge")  # operational kept
        self.assertEqual(out["payment_source"]["card"]["country_code"], "US")
        self.assertEqual(out["payment_source"]["card"]["last_digits"], "1111")
        self.assertEqual(pu["shipping"], {})  # PII container collapsed
        self.assertEqual(out["payer"], {})  # PII container collapsed
