# Windows setup

How to run the alert engine on a Windows desktop. Takes ~10 minutes.

The engine **watches** symbols and **pushes alerts** to your phone and desktop.
It never places orders.

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

`.env` contains your Alpaca keys and Discord bot configuration.
`settings_local.py` contains the screening settings. Without both, the engine
won't connect or screen correctly.

## 5. Set up Discord control and alerts

Follow `DISCORD_SETUP.md` once to create the private bot/channel and put its token
and numeric IDs in `.env`. Only allowlisted users in that server/channel can run
the market commands. Never share or commit the bot token.

## 6. Test it before you rely on it

Run the engine in **replay mode**, which pulls real recent market data and works
any time — nights, weekends, holidays. Do this **with your phone next to you**:

```powershell
python -m alertengine --replay --headless
```

Use `/watch AAPL`, `/status`, and `/watchlist` in the private Discord channel.
The bot should respond there and post alert embeds during replay. Fix bot access
before relying on it at market open.

## 7. Run it live

During market hours (**6:30 AM–1:00 PM Pacific**, Mon–Fri):

```powershell
python -m alertengine --live
```

Leave the window open. It streams live 1-minute bars, and pushes an alert the
moment a watched symbol hits the setup. Press **Ctrl+C** to stop.

## Daily use

1. Open PowerShell, `cd market-sentinel`
2. `.\.venv\Scripts\Activate.ps1`
3. `python -m alertengine --live`

That's it. Steps 1–5 are one-time; after that it's just those three lines each
morning.

## Troubleshooting

- **`python` not recognized** — the PATH box wasn't checked in step 1.
  Reinstall Python and check it, or use `py -m alertengine ...` instead.
- **Discord bot offline** — confirm the token and all IDs in `.env`, ensure the
  bot is in the server, and confirm it can View Channel, Send Messages, and
  Embed Links.
- **Connection / auth error at startup** — `.env` is missing or the Alpaca keys
  are wrong. Check the file is in the project root, not a subfolder.
- **Times look off** — timestamps are shown in Pacific; the `tzdata` package
  (installed in step 3) provides the timezone data Windows needs.
