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
