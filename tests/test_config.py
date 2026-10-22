from pathlib import Path

import pytest

from basis.config import load_config, load_secrets, parse_env_file


def test_parse_env_file_handles_comments_quotes_blanks() -> None:
    text = "# comment\n\nA=1\nB=\"two words\"\nC='x=y'\nnot a pair\n D = spaced \n"
    assert parse_env_file(text) == {"A": "1", "B": "two words", "C": "x=y", "D": "spaced"}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for k in [
        "KALSHI_PROD_KEY_ID",
        "KALSHI_PROD_PRIVATE_KEY_PATH",
        "KALSHI_DEMO_KEY_ID",
        "KALSHI_DEMO_PRIVATE_KEY_PATH",
        "ROBINHOOD_API_KEY",
        "ROBINHOOD_PRIVATE_KEY_B64",
        "ROBINHOOD_ACCOUNT_NUMBER",
        "NTFY_TOPIC",
        "CRYPTOBOT_SECRETS_FILE",
    ]:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_missing_credentials_are_none(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    s = load_secrets(tmp_path / "nope.env")
    assert s.kalshi_prod is None and s.kalshi_demo is None and s.robinhood is None


def test_env_overrides_file_and_repr_hides_secrets(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "secrets.env"
    f.write_text(
        "ROBINHOOD_API_KEY=from-file\nROBINHOOD_PRIVATE_KEY_B64=SECRETSEED\n"
        "KALSHI_PROD_KEY_ID=kid\nKALSHI_PROD_PRIVATE_KEY_PATH=~/k.pem\n"
    )
    clean_env.setenv("ROBINHOOD_API_KEY", "from-env")
    s = load_secrets(f)
    assert s.robinhood is not None and s.robinhood.api_key == "from-env"
    assert s.kalshi_prod is not None and s.kalshi_prod.key_id == "kid"
    assert "SECRETSEED" not in repr(s) and "from-env" not in repr(s)


def test_data_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CRYPTOBOT_DATA_DIR", str(tmp_path))
    assert load_config()["paths"]["data_dir"] == str(tmp_path)
