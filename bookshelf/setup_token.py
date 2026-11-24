"""The token the first-run wizard asks for, so only the app's operator can use it."""

import base64
import hashlib
import hmac

__all__ = ["setup_token", "token_matches"]

# The token's characters (base32: 5 bits each, 100 bits) and their group size.
_TOKEN_LENGTH = 20
_TOKEN_GROUP = 5


def setup_token(secret_key: str) -> str:
    """
    Derived from the secret key, so every process of a deployment asks for
    the same token, and only whoever can read the key, the app's log or run
    its CLI knows it. It is only good until an admin exists.
    """
    digest = hmac.new(
        secret_key.encode(), b"bookshelf setup token", hashlib.sha256
    ).digest()
    chars = base64.b32encode(digest).decode()[:_TOKEN_LENGTH]
    return "-".join(
        chars[i : i + _TOKEN_GROUP] for i in range(0, _TOKEN_LENGTH, _TOKEN_GROUP)
    )


def token_matches(typed: str, secret_key: str) -> bool:
    """Whether the typed token is the right one, whatever its case, spaces or dashes."""
    expected = _normalise(setup_token(secret_key))
    return hmac.compare_digest(_normalise(typed).encode(), expected.encode())


def _normalise(token: str) -> str:
    return "".join(token.split()).replace("-", "").upper()
