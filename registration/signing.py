"""Centralized signed-token contexts (Decision #10 peer-review ATTACK-1/2).

Every ``TimestampSigner`` in this codebase previously used the default
salt, so a token minted for ANY signed-object purpose validated for ANY
other (the print-capability URL blob could be replayed as a
``terminal-token`` cookie → MED-2/HIGH-2 bypass). This module is the ONE
place signed-token salts and the terminal-token payload shape live, so an
issuer and its verifier can never silently desync and contexts cannot be
confused.

- Distinct, explicit ``salt`` per purpose (issuer + matching verifier
  share it; unrelated contexts differ).
- The terminal-token is bound to the terminal's immutable **id** and a
  **rotation epoch** derived from its token hash — so it is not bound to
  the mutable ``name`` and a leaked cookie is invalidated the moment the
  terminal's bearer token is rotated (the documented compromise
  response). The payload shape is validated on verify (an object carrying
  unexpected keys is rejected).
"""

from django.core.signing import TimestampSigner

# Bump the version suffix to hard-invalidate every outstanding token of a
# context (e.g. on a security incident) without touching SECRET_KEY.
_TERMINAL_TOKEN_SALT = "apis.terminal-token.v1"
_PRINT_CAPABILITY_SALT = "apis.print-capability.v1"


def _terminal_epoch(terminal) -> str:
    """Rotation marker: changes whenever the terminal's bearer token is
    (re)minted, because ``token_hash`` changes (Decision #8). A leaked
    terminal-token cookie therefore dies on rotation."""
    return (getattr(terminal, "token_hash", "") or "")[:16]


def mint_terminal_token(terminal) -> str:
    """Issue a terminal-token bound to the terminal id + rotation epoch."""
    return TimestampSigner(salt=_TERMINAL_TOKEN_SALT).sign_object(
        {"v": 1, "tid": terminal.id, "epoch": _terminal_epoch(terminal)}
    )


def resolve_terminal_token(token: str | None, max_age: int):
    """Return the bound ``Firebase`` terminal for a valid token, else None.

    Fails closed on: absent/garbage token, bad signature, expiry, wrong
    shape, unknown terminal, or a stale rotation epoch.
    """
    if not token:
        return None
    try:
        data = TimestampSigner(salt=_TERMINAL_TOKEN_SALT).unsign_object(token, max_age=max_age)
    except Exception:
        return None
    # Strict shape: exactly the keys we mint, correct types. Reject a blob
    # produced by any other signing context (e.g. the print payload).
    if not isinstance(data, dict) or set(data) != {"v", "tid", "epoch"}:
        return None
    if data.get("v") != 1 or not isinstance(data.get("tid"), int):
        return None
    # Import here: this module must stay import-pure for use by views at
    # module load (mirrors clientip's discipline); the model import is
    # call-time only.
    from registration.models import Firebase

    terminal = Firebase.objects.filter(id=data["tid"]).first()
    if terminal is None:
        return None
    if data.get("epoch") != _terminal_epoch(terminal):
        return None
    return terminal


def print_capability_signer() -> TimestampSigner:
    """Signer for short-lived print-capability URL payloads (onsite badge
    print / admin nametag). Distinct salt from the terminal-token context
    so the two can never be cross-replayed."""
    return TimestampSigner(salt=_PRINT_CAPABILITY_SALT)
