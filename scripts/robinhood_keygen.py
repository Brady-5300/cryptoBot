"""Generate a Robinhood Crypto API Ed25519 key pair.

Writes the private key straight into the secrets file (never printed, never in the repo) and
prints only the PUBLIC key, which you paste into Robinhood when creating API credentials.

    python scripts/robinhood_keygen.py            # refuses to overwrite an existing key
    python scripts/robinhood_keygen.py --force    # replace it (old credential stops working)
"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from basis.config import DEFAULT_SECRETS_FILE, parse_env_file

KEY = "ROBINHOOD_PRIVATE_KEY_B64"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="overwrite an existing private key")
    args = ap.parse_args()

    path = Path(os.environ.get("CRYPTOBOT_SECRETS_FILE", DEFAULT_SECRETS_FILE))
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if parse_env_file(text).get(KEY) and not args.force:
        raise SystemExit(f"{KEY} is already set in {path}; use --force to replace it")

    key = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(key.private_bytes_raw()).decode()
    public_b64 = base64.b64encode(key.public_key().public_bytes_raw()).decode()

    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(f"{KEY}=")]
    lines.append(f"{KEY}={private_b64}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Private key saved to {path}")
    print("Paste this PUBLIC key into Robinhood when creating the API credential:\n")
    print(public_b64)


if __name__ == "__main__":
    main()
