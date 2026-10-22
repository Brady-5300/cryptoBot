# basis — Kalshi perps vs. Robinhood spot basis engine

One event-driven codebase for recording, backtesting, paper trading and (later, with explicit
approval) live trading. **Current state: Milestone 0, the data recorder and backfill.** Nothing
here places orders.

## Layout

```
config/            base.toml (non-secret settings), secrets.example.env
src/basis/
  config.py        TOML config + secrets (env vars / file outside the repo)
  core/            clock (wall + monotonic), fixed-point money, token bucket, RawResponse
  venues/kalshi/   env URLs, request signing (RSA-PSS / Ed25519), REST, WebSocket (+seq gaps)
  venues/robinhood/ official Crypto Trading API: Ed25519 signing, REST (v1 + v2)
  data/rawlog.py   append-only raw log, hourly rotation, zstd compression, crash-safe reader
  data/recorder.py 24/7 recorder (Kalshi WS + public REST, Robinhood quotes)
  data/backfill.py public Kalshi history -> Parquet (1m candles, funding, trades)
  ops/             check (read-only env check), alerts (ntfy.sh)
scripts/           macos/recorder_agent.sh (launchd), Windows task scripts, robinhood_keygen.py
tests/             unit tests; `-m network` hits public Kalshi endpoints
```

## Setup (Windows dev laptop; for the Mac recorder see docs/recorder-laptop-setup.md)

The repo sits in OneDrive, so keep the venv, the data and the secrets **outside** it:

```powershell
py -3.14 -m venv $env:USERPROFILE\.venvs\cryptobot
& $env:USERPROFILE\.venvs\cryptobot\Scripts\python.exe -m pip install -e ".[dev]"
```

Secrets: copy `config/secrets.example.env` to `%USERPROFILE%\.cryptobot\secrets.env` and fill in
what you have. Every source is optional:

| Credentials        | Recorded                                                            |
|--------------------|---------------------------------------------------------------------|
| none               | Kalshi `/margin/markets` @1 Hz (reference, marks, top of book), funding estimates |
| Kalshi margin key  | + WebSocket ticker / trades / full order book deltas                 |
| Robinhood API key  | + v1 and v2 best bid/ask, size-aware `estimated_price` ladders        |

## Run

```powershell
basis-check                    # read-only: connectivity, keys, /margin/enabled, clock
basis-backfill                 # 1m candles + funding history for all perps
basis-record                   # Ctrl+C to stop; --duration N for a timed run
```

The 24/7 recorder runs on a spare **Mac**: follow
[docs/recorder-laptop-setup.md](docs/recorder-laptop-setup.md). It installs a launchd agent via
`scripts/macos/recorder_agent.sh`. (`scripts/*.ps1` do the same on Windows with a scheduled
task.) Set `NTFY_TOPIC` to get phone alerts when a stream goes stale or auth fails.

## Raw data format

`<data_dir>/raw/<venue>/<stream>/<YYYY-MM-DD>/<HH>.jsonl[.zst]`, one record per line:
`{"t": wall_ns_received, "m": monotonic_ns, "d": <message verbatim>}`. Polled REST responses
are wrapped as `{"path", "sent_ns", "status", "body"}`. `meta` streams log connects,
disconnects, sequence gaps and HTTP errors so replay knows where order-book state is invalid.
Replay orders by `t` (local receive time), never by exchange time.

## Tests

```powershell
python -m pytest              # unit tests
python -m pytest -m network   # also hit public Kalshi endpoints
python -m ruff check src tests
python -m mypy                # strict; see note below
```

Note: on this machine Windows Smart App Control blocks mypy's compiled extension (`librt`),
so mypy can't run here. It's configured (`mypy --strict`) for any machine where it loads.
