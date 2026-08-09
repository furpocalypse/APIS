"""Pure, import-time security configuration assertions.

Kept out of ``settings.py`` so the logic is unit-testable without importing
the whole settings module (which would re-run every other import-time
guard). ``settings.py`` calls these under its existing ``_IS_PROD`` gate so
a misconfigured production process fails closed at boot.
"""

import base64
import binascii

# HMAC-SHA256 key floor: a key shorter than the hash output (32 bytes) does
# not add security and makes the MQTT push JWT forgeable.
MQTT_MIN_KEY_BYTES = 32


def assert_strong_mqtt_secret(secret: str | None) -> None:
    """MED-5 (OWASP A02 / ASVS V6.1.1).

    ``registration.mqtt`` signs push tokens with ``base64decode(secret)``
    under HS256. Reject a missing, non-base64, or under-strength secret.

    Raises:
        RuntimeError: if ``secret`` is unset, not valid base64, or decodes
            to fewer than ``MQTT_MIN_KEY_BYTES`` bytes.
    """
    try:
        decoded = base64.b64decode(secret or b"", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(
            "MQTT_JWT_SECRET must be valid base64 when APIS_ENV=production."
        ) from exc
    if len(decoded) < MQTT_MIN_KEY_BYTES:
        raise RuntimeError(
            f"MQTT_JWT_SECRET must decode to >= {MQTT_MIN_KEY_BYTES} bytes "
            "(the HMAC-SHA256 key floor) when APIS_ENV=production; "
            "configure a stronger secret."
        )


# The documented copy-the-example placeholder for TRUSTED_PROXY_CIDRS
# (.env.production.example / aks overlay). Shipping it unchanged silently
# disables/​mis-scopes MED-13's peer-CIDR anti-spoof check.
TRUSTED_PROXY_PLACEHOLDER = "10.224.0.0/16"


PAYPAL_VALID_ENVIRONMENTS = ("production", "sandbox")


def assert_complete_paypal_config(
    *,
    client_id: str | None,
    client_secret: str | None,
    webhook_id: str | None,
    environment: str | None,
) -> None:
    """PayPal configuration must be all-or-nothing in production.

    ``verify_signature`` fails closed when ``PAYPAL_WEBHOOK_ID`` or the
    client credentials are empty, so a partially configured deployment
    rejects 100% of webhook deliveries with 403 while booting green and
    passing every healthcheck. Fail loud at boot instead. A deployment
    with all three values empty legitimately does not use PayPal and is
    allowed.

    ``PAYPAL_ENVIRONMENT`` is validated whenever PayPal is configured:
    the codebase selects live vs sandbox by prefix-matching "p", so an
    unrecognized value (e.g. "live", or an accidentally empty string)
    silently routes verification to the sandbox API — which rejects every
    live webhook.

    Raises:
        RuntimeError: if some but not all of the three PayPal values are
            set, or if PayPal is configured with an unrecognized
            ``PAYPAL_ENVIRONMENT``.
    """
    values = {
        "PAYPAL_CLIENT_ID": client_id or "",
        "PAYPAL_CLIENT_SECRET": client_secret or "",
        "PAYPAL_WEBHOOK_ID": webhook_id or "",
    }
    missing = sorted(name for name, value in values.items() if not value)
    if missing and len(missing) != len(values):
        raise RuntimeError(
            "Partial PayPal configuration when APIS_ENV=production: "
            f"{', '.join(missing)} empty while other PAYPAL_* values are "
            "set. verify_signature fails closed on incomplete config, so "
            "every webhook delivery would be rejected 403. Set all of "
            "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET / PAYPAL_WEBHOOK_ID, "
            "or clear all three to run without PayPal."
        )
    if not missing and (environment or "").lower() not in PAYPAL_VALID_ENVIRONMENTS:
        raise RuntimeError(
            f"PAYPAL_ENVIRONMENT must be one of {PAYPAL_VALID_ENVIRONMENTS} "
            f"when PayPal is configured (got {environment!r}). An "
            "unrecognized value silently selects the sandbox API base and "
            "every live webhook fails verification."
        )


def assert_no_placeholder_proxy_cidrs(cidrs) -> None:
    """Peer-review (General Opinion / Adversarial): fail loud if the
    documented TRUSTED_PROXY_CIDRS placeholder was shipped unchanged.

    An EMPTY list is intentionally allowed — it is the legitimate T1
    (Cloudflare→nginx origin-lock) production posture. Only the unmodified
    placeholder is a definite misconfiguration.

    Raises:
        RuntimeError: if ``TRUSTED_PROXY_PLACEHOLDER`` is in ``cidrs``.
    """
    if TRUSTED_PROXY_PLACEHOLDER in (cidrs or []):
        raise RuntimeError(
            "TRUSTED_PROXY_CIDRS still contains the documented placeholder "
            f"{TRUSTED_PROXY_PLACEHOLDER!r}. Set the real upstream proxy "
            "CIDR (docs/deploy-preflight.md MED-12/MED-13) or, for the T1 "
            "nginx-origin-lock topology, set TRUSTED_PROXY_CIDRS empty "
            "deliberately."
        )
