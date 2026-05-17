"""MED-8 (OWASP A02 / ASVS V6.2.1 / Privacy cheat sheet).

``Order.apiData`` stored the *entire* Square / PayPal API response. No PAN
(providers never return it — PCI-DSS), but billing / shipping / payer
names, addresses, emails and phone numbers — a DB read leaked all of it.

Decision #12b: **field reduction by PII-stripping**, not encryption and
not a 3-field gut. The operational structure the payment lifecycle needs
(payment / order / refund / dispute ids, statuses, amounts, card last-4,
``purchase_units``, ``refunds``, ``dispute``) is preserved; only the
customer-PII fields are removed. This keeps refund / dispute / refresh /
onsite-capture working while removing the leak.

The walk is deny-list based and recursive. PII *container* keys (``payer``,
``shipping``, address blocks) collapse to ``{}`` so downstream
``dict.get`` / SDK ``from_dictionary`` calls still see a dict; PII *scalar*
keys collapse to ``"[redacted]"``. Operational identifiers (``id``,
``status``, ``amount``, ``last_4`` / ``last4``, ``card_brand`` …) are NOT
in the deny-list and pass through untouched.
"""

import json

# Whole sub-objects that are customer PII end-to-end → drop to ``{}``.
PII_CONTAINER_KEYS = frozenset(
    {
        "payer",
        "shipping",
        "shipping_address",
        "shipping_details",
        "billing_address",
        "billing_details",
        "address",
        "buyer_address",
        "recipient",
    }
)

# Individual PII scalar fields anywhere in the tree → ``"[redacted]"``.
PII_SCALAR_KEYS = frozenset(
    {
        "email_address",
        "buyer_email_address",
        "email",
        "phone_number",
        "phone",
        "given_name",
        "family_name",
        "surname",
        "full_name",
        "cardholder_name",
        "address_line_1",
        "address_line_2",
        "address_line1",
        "address_line2",
        "locality",
        "sublocality",
        "postal_code",
        "administrative_district_level_1",
        "administrative_district_level_2",
        "admin_area_1",
        "admin_area_2",
        # Peer-review non-block-3 / Red Team ATTACK-5: ``name`` and
        # ``country_code`` were intentionally REMOVED from the global
        # scalar deny-list. They collide with operational provider data
        # (e.g. PayPal ``purchase_units[].items[].name`` = SKU/product
        # name, card issuing ``country_code`` used for fraud rules),
        # which the module's contract promises to preserve. The PII
        # instances of these keys live inside the PII *container* keys
        # below (``payer.name``, ``shipping.address.country_code`` …),
        # which collapse to ``{}`` wholesale — so personal names/countries
        # are still removed, without corrupting operational fields. The
        # specific personal-name scalars (given/family/surname/full_name/
        # cardholder_name) remain denied.
    }
)

_REDACTED = "[redacted]"


def _strip(value):
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            lkey = key.lower()
            if lkey in PII_CONTAINER_KEYS:
                out[key] = {}
            elif lkey in PII_SCALAR_KEYS:
                out[key] = {} if isinstance(val, (dict, list)) else _REDACTED
            else:
                out[key] = _strip(val)
        return out
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def sanitize_api_data(data):
    """Return ``data`` with customer PII stripped, shape preserved.

    Accepts a dict, a list, a JSON string, or ``None``. A JSON string is
    parsed, stripped, and re-serialized so the column's storage type is
    unchanged; a non-JSON string (e.g. a raw provider error body) is
    returned as ``"[redacted: unparseable provider body]"`` because such
    bodies are unstructured and may embed PII we cannot selectively remove.
    """
    if data is None:
        return None
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except (ValueError, TypeError):
            return "[redacted: unparseable provider body]"
        return json.dumps(_strip(parsed))
    return _strip(data)
