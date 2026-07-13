# Optional Windows development setup

How to run the alert engine locally for development/replay. Production already
runs on EC2 and is controlled through Discord.

The local process prints alerts in its REPL. It never places orders.

## 1. Install Python

Download **Python 3.12** from <https://www.python.org/downloads/> and run the
installer. On the first screen, **check "Add python.exe to PATH"** before
clicking Install — this is easy to miss and everything else depends on it.

Verify in a new PowerShell window:

```powershell
py --version        # should print Python 3.12.x
```

## 2. Get the code

```powershell
git clone <repo-url> market-sentinel
cd market-sentinel
```

## 3. Create a virtual environment and install

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

If PowerShell blocks the activate script with an execution-policy error, run
this once, then retry the activate line:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Your prompt should now start with `(.venv)`.

## 4. Add the two config files

These are **not** in the repo (they hold keys and private settings). You'll get
them separately. Place them exactly here:

| File                              | Goes in                     |
| --------------------------------- | --------------------------- |
| `.env`                            | project root (next to `pyproject.toml`) |
| `settings_local.py`               | `alertengine\` folder       |

`.env` needs the Alpaca keys for live/replay. `settings_local.py` contains the
private screening settings. Discord values are needed only if intentionally
running `--headless`; do not start a second headless process while production is
using the same bot token.

## 5. Discord is already the production control

See `DISCORD_SETUP.md` for the EC2 configuration. Normal Windows development
uses the local REPL and does not connect another Discord Gateway session.

## 6. Test it before you rely on it

Run replay mode, which pulls recent market data and works nights, weekends, and
holidays:

```powershell
python -m alertengine --replay
```

Use the REPL: `approve AAPL`, `watch`, `status`, then `stop`/`quit`. Replay alerts
are clearly isolated from the production Discord bot.

## 7. Run it live

During market hours (**6:30 AM–1:00 PM Pacific**, Mon–Fri):

```powershell
python -m alertengine --live
```

Leave the window open. It streams live 1-minute bars and prints alerts locally.
Press **Ctrl+C** to stop. Production operation should normally remain on EC2.

## Daily use

1. Open PowerShell, `cd market-sentinel`
2. `.\.venv\Scripts\Activate.ps1`
3. `python -m alertengine --live`

That's it. Steps 1–5 are one-time; after that it's just those three lines each
morning.

## Troubleshooting

- **`python` not recognized** — the PATH box wasn't checked in step 1.
  Reinstall Python and check it, or use `py -m alertengine ...` instead.
- **Connection / auth error at startup** — `.env` is missing or the Alpaca keys
  are wrong. Check the file is in the project root, not a subfolder.
- **Times look off** — timestamps are shown in Pacific; the `tzdata` package
  (installed in step 3) provides the timezone data Windows needs.
