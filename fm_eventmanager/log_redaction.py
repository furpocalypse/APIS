"""MED-6 (OWASP A09 / ASVS V7.1.1 / Logging cheat sheet).

A central ``logging.Filter`` that pattern-redacts the most common PII /
sensitive tokens from formatted log lines before they reach a handler.

This is a *floor-raiser*, not a guarantee: structured scrubbing at the call
site (and never logging PII in the first place — see HIGH-4 / S33-g) remains
the primary control. This filter catches accidental leaks that slip through
``logger.*`` calls which, unlike Sentry's ``before_send``, otherwise flow
straight to stdout unmodified.

Design constraints:
- Never raise. A logging filter that throws would break the very call it is
  meant to protect, so every code path fails open (returns the record).
- Operate on the *fully rendered* message (``record.getMessage()``), then
  clear ``record.args`` so the handler does not re-interpolate originals.
- Conservative patterns — over-redaction of an ID is acceptable; leaking an
  email/phone/PAN is not.
"""

import logging
import re

# Email addresses (RFC-pragmatic, not RFC-complete - deliberately broad).
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.UNICODE)

# Credit-card-like PANs: 13-19 digits, optionally separated by spaces or
# dashes. Checked before phone numbers so a 16-digit card is not partly
# eaten by the phone pattern.
_PAN_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

# Phone numbers: an optional leading +, then 9-17 chars of digits and the
# usual separators, anchored on word boundaries. Loose enough to catch
# E.164 and common national formats without devouring short numerics.
_PHONE_RE = re.compile(r"(?<!\w)\+?\d(?:[\d ().-]{7,15})\d(?!\w)")

_SUBSTITUTIONS = (
    (_EMAIL_RE, "[redacted-email]"),
    (_PAN_RE, "[redacted-pan]"),
    (_PHONE_RE, "[redacted-phone]"),
)


def redact(text: str) -> str:
    """Return ``text`` with email / PAN / phone substrings masked."""
    for pattern, replacement in _SUBSTITUTIONS:
        text = pattern.sub(replacement, text)
    return text


class PIIRedactingFilter(logging.Filter):
    """Mask PII in the rendered message of every record that passes through."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            redacted = redact(message)
            if redacted != message:
                record.msg = redacted
                record.args = ()
        except Exception:
            # Fail open: a redaction bug must never suppress a log line.
            pass
        return True
