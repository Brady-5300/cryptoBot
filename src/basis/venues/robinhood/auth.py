"""Robinhood Crypto Trading API request signing (official API only).

Headers: ``x-api-key``, ``x-timestamp`` (Unix **seconds**, valid for 30s), ``x-signature``.
Message: ``f"{api_key}{timestamp}{path}{method}{body}"`` where ``path`` includes the query
string and ``body`` is the exact request body text (omitted when there is none). Signed with
the account's Ed25519 key (base64 32-byte seed); signature is base64.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class RobinhoodSigner:
    def __init__(self, api_key: str, private_key_b64: str) -> None:
        self.api_key = api_key
        self._key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))

    def signature(self, timestamp: int, path: str, method: str, body: str = "") -> str:
        message = f"{self.api_key}{timestamp}{path}{method.upper()}{body}".encode()
        return base64.b64encode(self._key.sign(message)).decode()

    def headers(self, timestamp: int, path: str, method: str, body: str = "") -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "x-timestamp": str(timestamp),
            "x-signature": self.signature(timestamp, path, method, body),
        }

    def __repr__(self) -> str:
        return "RobinhoodSigner(<redacted>)"
