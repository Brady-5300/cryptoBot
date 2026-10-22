# Recorder Mac setup

Sets up the spare MacBook to record market data 24/7. Recorded data lives only on the Mac, in
`~/cryptobot-data`. Nothing here places orders.

Run all commands in **Terminal** on the Mac unless a step says otherwise.

## On the Windows laptop: pack a USB stick

1. Copy the repo folder `Desktop\cryptoBot` to the USB stick.
2. Copy the keys folder `C:\Users\99kil\.cryptobot` to the USB stick. It contains `secrets.env`
   and `kalshi-prod.pem`. Because the name starts with a dot, Finder on the Mac hides it; press
   Cmd+Shift+. to show it.

The keys never go through OneDrive, iCloud, email or chat.

## On the Mac

### 1. Copy the files

Use exactly these locations. macOS blocks background jobs from reading **Desktop, Documents,
Downloads and iCloud Drive**, so the repo must not live there.

```bash
cp -R /Volumes/<USB-NAME>/cryptoBot ~/cryptobot
cp -R /Volumes/<USB-NAME>/.cryptobot ~/.cryptobot
chmod 700 ~/.cryptobot && chmod 600 ~/.cryptobot/*
```

Then **delete both folders from the USB stick**. Use Finder and press Cmd+Shift+. to show the
hidden `.cryptobot` folder.

`secrets.env` already points at `~/.cryptobot/kalshi-prod.pem`, so no edits are needed.

### 2. Install Python 3.14 and the project

1. Install Python 3.14 with the macOS installer from python.org. After it finishes, double-click
   **Install Certificates.command** in `/Applications/Python 3.14/`. Without that step, HTTPS
   calls can fail.
2. Create the virtual environment and install:
   ```bash
   python3.14 -m venv ~/.venvs/cryptobot
   ~/.venvs/cryptobot/bin/python -m pip install -e "$HOME/cryptobot[dev]"
   ```
3. Run the tests. All of them should pass:
   ```bash
   cd ~/cryptobot && ~/.venvs/cryptobot/bin/python -m pytest -q
   ```

### 3. Check keys and connectivity

```bash
~/.venvs/cryptobot/bin/basis-check
```

Expected results:

- **Kalshi:** `[ok]` for all four prod perps and for API limits.
  `margin enabled: {"enabled": false}`, together with the balance and fee `[FAIL]` lines, is
  expected until Kalshi enables perps trading on your account. It doesn't affect recording.
- **Robinhood:** `[ok]` on all five lines.
- **Clock:** the clock line should be `[ok]`.

### 4. Keep the Mac awake, on and on time

```bash
sudo pmset -a disablesleep 1     # don't sleep, even with the lid closed
sudo pmset -a autorestart 1      # power back on after a power failure
sudo sntp -sS time.apple.com     # sync the clock now
```

Also set these in System Settings:

- **General → Date & Time:** turn on *Set time and date automatically*.
- **Users & Groups (or Lock Screen):** turn on automatic login if it's offered.

**FileVault caveat.** The recorder runs as a LaunchAgent, which only runs while you're logged
in. If FileVault is on, macOS won't log in by itself after a reboot, so after any restart (an
update or power cut) you must log in once and the recorder then starts by itself. Phone alerts
(step 7) tell you when that's needed. Keeping FileVault on is the safer choice; it protects your
keys if the Mac is stolen.

Keep the Mac plugged in. If you close the lid, leave it somewhere ventilated. To undo the sleep
setting later: `sudo pmset -a disablesleep 0`.

### 5. Install the recorder as a background service

```bash
bash ~/cryptobot/scripts/macos/recorder_agent.sh install
```

This starts the recorder now, at every login, and again within 30 s if it ever crashes. Day to
day, the same script also takes `status`, `restart` and `uninstall`.

### 6. Verify it's recording

Wait about 2 minutes, then run:

```bash
bash ~/cryptobot/scripts/macos/recorder_agent.sh status   # state = running
cat ~/cryptobot-data/recorder.heartbeat; echo             # wall_ns keeps increasing
tail -n 20 ~/cryptobot-data/logs/recorder.log             # "lines in last 60s: {...}" every minute
find ~/cryptobot-data/raw -maxdepth 2 -type d             # kalshi/ws, kalshi/rest_markets, robinhood/bba_v1, ...
```

The log should show these streams, all with non-zero counts:

- `kalshi/ws`: thousands per minute
- `kalshi/rest_markets`: about 60
- `robinhood/bba_v1` and `robinhood/bba_v2`: about 20 each

### 7. Backfill Kalshi history (once)

```bash
~/.venvs/cryptobot/bin/basis-backfill
```

This takes a few minutes and writes to `~/cryptobot-data/backfill`.

### 8. Phone alerts (recommended)

1. Install the **ntfy** app on your phone.
2. Subscribe to a long, random topic name, e.g. `cryptobot-7f3k9q2xw`. Anyone who knows the
   topic name can read the alerts.
3. Add `NTFY_TOPIC=<that name>` to `~/.cryptobot/secrets.env`.
4. Restart the recorder:
   ```bash
   bash ~/cryptobot/scripts/macos/recorder_agent.sh restart
   ```

You'll then get a push notification if a data stream goes silent for 2 minutes or a key stops
working.

## Day to day

| Task | Command |
|---|---|
| Is it alive? | `bash ~/cryptobot/scripts/macos/recorder_agent.sh status` |
| Disk use | `du -sh ~/cryptobot-data` (about 2.5 GB/day); free space: `df -h ~` |
| Update the code | Copy the new code over `~/cryptobot`, then run `... recorder_agent.sh restart` |
| Stop for good | `bash ~/cryptobot/scripts/macos/recorder_agent.sh uninstall` |

## Troubleshooting

- **`status` says not running, or the last exit code is not 0:** check
  `tail -n 50 ~/cryptobot-data/logs/launchd.err.log` and `recorder.log`.
- **"Operation not permitted" in the logs:** the repo or data folder ended up under
  Desktop/Documents/Downloads/iCloud. Move it (step 1) and run `install` again.
- **Kalshi `handshake_rejected 401` in the logs:** the key ID or `.pem` in
  `~/.cryptobot` is wrong or missing.
- **SSL / certificate errors:** run *Install Certificates.command* (step 2.1).
