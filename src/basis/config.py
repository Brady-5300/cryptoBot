"""Configuration and secrets loading.

Non-secret settings come from TOML files in ``config/``. Secrets come from the process
environment, optionally seeded from a ``KEY=VALUE`` file that lives outside the repo
(default ``~/.cryptobot/secrets.env``). Real environment variables always win.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "base.toml"
DEFAULT_SECRETS_FILE = Path.home() / ".cryptobot" / "secrets.env"


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(*paths: Path) -> dict[str, Any]:
    """Load ``base.toml`` then merge each extra TOML file on top, in order."""
    cfg: dict[str, Any] = {}
    for p in (DEFAULT_CONFIG, *paths):
        with p.open("rb") as f:
            cfg = _deep_merge(cfg, tomllib.load(f))
    if env_dir := os.environ.get("CRYPTOBOT_DATA_DIR"):
        cfg.setdefault("paths", {})["data_dir"] = env_dir
    return cfg


def parse_env_file(text: str) -> dict[str, str]:
    """Parse a minimal dotenv file: ``KEY=VALUE`` lines, ``#`` comments, optional quotes."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _secret_source(secrets_file: Path | None) -> dict[str, str]:
    path = secrets_file or Path(os.environ.get("CRYPTOBOT_SECRETS_FILE", DEFAULT_SECRETS_FILE))
    file_values = parse_env_file(path.read_text(encoding="utf-8")) if path.exists() else {}
    # Real environment wins over the file.
    return {**file_values, **{k: v for k, v in os.environ.items() if v}}


@dataclass(frozen=True)
class KalshiCredentials:
    key_id: str
    private_key_path: Path = field(repr=False)


@dataclass(frozen=True)
class RobinhoodCredentials:
    api_key: str = field(repr=False)
    private_key_b64: str = field(repr=False)
    account_number: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Secrets:
    kalshi_prod: KalshiCredentials | None
    kalshi_demo: KalshiCredentials | None
    robinhood: RobinhoodCredentials | None
    ntfy_topic: str | None = field(default=None, repr=False)

    def kalshi(self, env: str) -> KalshiCredentials | None:
        return {"prod": self.kalshi_prod, "demo": self.kalshi_demo}[env]


def load_secrets(secrets_file: Path | None = None) -> Secrets:
    """Load whatever credentials are present. Missing credentials yield ``None``, not errors,
    so the recorder can run on public data alone."""
    src = _secret_source(secrets_file)

    def kalshi(prefix: str) -> KalshiCredentials | None:
        key_id, key_path = src.get(f"{prefix}_KEY_ID"), src.get(f"{prefix}_PRIVATE_KEY_PATH")
        if not (key_id and key_path):
            return None
        return KalshiCredentials(key_id, Path(key_path).expanduser())

    rh = None
    if (api_key := src.get("ROBINHOOD_API_KEY")) and (pk := src.get("ROBINHOOD_PRIVATE_KEY_B64")):
        rh = RobinhoodCredentials(api_key, pk, src.get("ROBINHOOD_ACCOUNT_NUMBER") or None)

    return Secrets(
        kalshi_prod=kalshi("KALSHI_PROD"),
        kalshi_demo=kalshi("KALSHI_DEMO"),
        robinhood=rh,
        ntfy_topic=src.get("NTFY_TOPIC") or None,
    )
