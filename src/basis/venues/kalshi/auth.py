"""Kalshi request signing.

Headers: ``KALSHI-ACCESS-KEY``, ``KALSHI-ACCESS-TIMESTAMP`` (Unix ms), ``KALSHI-ACCESS-SIGNATURE``.
Message: ``f"{timestamp_ms}{METHOD}{path}"`` where ``path`` is the full path from the host root
(including ``/trade-api/v2``) **without** the query string. RSA keys sign with RSA-PSS/SHA-256
(salt length = digest length); Ed25519 keys sign the message directly. Signature is base64.
"""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

type PrivateKey = rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey


def load_private_key(pem: bytes) -> PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey):
        raise TypeError(f"unsupported Kalshi key type: {type(key).__name__}")
    return key


def sign_message(key: PrivateKey, message: bytes) -> bytes:
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return key.sign(message)
    return key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


class KalshiSigner:
    def __init__(self, key_id: str, key: PrivateKey) -> None:
        self.key_id = key_id
        self._key = key

    @classmethod
    def from_file(cls, key_id: str, path: Path) -> KalshiSigner:
        return cls(key_id, load_private_key(path.read_bytes()))

    def headers(self, method: str, path: str, timestamp_ms: int) -> dict[str, str]:
        path = path.split("?", 1)[0]
        message = f"{timestamp_ms}{method.upper()}{path}".encode()
        signature = base64.b64encode(sign_message(self._key, message)).decode()
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": signature,
        }

    def __repr__(self) -> str:
        return f"KalshiSigner(key_id={self.key_id!r})"
