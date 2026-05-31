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

# Only PAN/phone need the IP/timestamp carve-out (a dotted-quad / ISO
# digit-run false-matches them). Email is NEVER an audit where/when
# field, so it is redacted over the FULL text up-front — otherwise an
# IP-literal email domain (admin@192.168.1.1) would have its domain
# protected as an "IPv4 span" and the email would escape redaction
# (peer-review Blue Team F4 regression).
_SUBSTITUTIONS = (
    (_PAN_RE, "[redacted-pan]"),
    (_PHONE_RE, "[redacted-phone]"),
)

# Peer-review (Blue Team F1/F2): a client IP and an event timestamp are
# the "where"/"when" of a security audit line — NOT PII this redactor is
# chartered to remove. But a bare IPv4 dotted-quad matches _PHONE_RE and
# an ISO-8601 timestamp's digit runs match _PHONE_RE/_PAN_RE, so naive
# redaction destroyed the forensic value of the MED-13 / unresolvable-IP
# / webhook-age reject lines. Spans matching these are protected:
# substitutions are applied only OUTSIDE them. (IPv6 already survived.)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_ISO_DT_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)


def redact(text: str) -> str:
    """Mask email / PAN / phone substrings, leaving client IPs and
    ISO-8601 timestamps (audit "where"/"when") intact."""
    # Email first, over the WHOLE string (incl. any IP-literal domain) —
    # email is not an audit where/when value, so it gets no IP/timestamp
    # carve-out (Blue Team F4).
    text = _EMAIL_RE.sub("[redacted-email]", text)
    # Then PAN/phone, protecting IPv4 / ISO-datetime spans so an attacker
    # source IP or reject timestamp is never collapsed into a redaction.
    protected = sorted(
        (m.start(), m.end()) for rx in (_IPV4_RE, _ISO_DT_RE) for m in rx.finditer(text)
    )
    out = []
    cursor = 0
    for start, end in protected:
        if start < cursor:  # overlapping span already covered
            continue
        segment = text[cursor:start]
        for pattern, replacement in _SUBSTITUTIONS:
            segment = pattern.sub(replacement, segment)
        out.append(segment)
        out.append(text[start:end])  # protected, verbatim
        cursor = end
    tail = text[cursor:]
    for pattern, replacement in _SUBSTITUTIONS:
        tail = pattern.sub(replacement, tail)
    out.append(tail)
    return "".join(out)


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
