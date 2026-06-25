"""Ed25519 signature verification for Discord interaction webhooks.

Discord signs every interaction POST with an Ed25519 signature over
(timestamp + body). This module provides a pure verification function
that never raises to callers — any error is surfaced as a False return.
"""

from __future__ import annotations

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


def verify_signature(
    public_key_hex: str, signature_hex: str, timestamp: str, body: bytes
) -> bool:
    """Verify Discord Ed25519 signature over (timestamp + body).

    Parameters
    ----------
    public_key_hex:
        The Discord application public key as a hex string (64 hex chars / 32 bytes).
    signature_hex:
        The value of the X-Signature-Ed25519 header (128 hex chars / 64 bytes).
    timestamp:
        The value of the X-Signature-Timestamp header.
    body:
        The raw request body bytes.

    Returns
    -------
    bool
        True if the signature is valid; False for any invalid, missing, or
        malformed input (bad hex, wrong length, verification failure, etc.).
    """
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex))
        message = timestamp.encode("utf-8") + body
        verify_key.verify(message, bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, ValueError, TypeError, Exception):
        return False
