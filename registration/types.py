from decimal import Decimal
from typing import Literal, TypedDict

type BillingData = dict[
    Literal[
        "source_id",
        "cc_firstname",
        "cc_lastname",
        "email",
        "address1",
        "address2",
        "city",
        "state",
        "postal",
        "country",
        "verificationToken",
    ],
    str,
]


class TranslatedCartItem(TypedDict):
    """
    Represents OrderItems translated to a format more useful to processing
    new PayPal order data.
    """

    name: str
    total: Decimal
    donation: bool
