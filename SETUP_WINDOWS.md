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

`.env` contains your Alpaca keys and the `NTFY_TOPIC`. `settings_local.py`
contains the screening settings. Without both, the engine won't connect or
screen correctly.

## 5. Set up push notifications (ntfy)

Alerts are delivered through **ntfy**, a free push service. Subscribe every
device you want buzzed to the topic name (it's the `NTFY_TOPIC` value in `.env`).

- **Phone (for when you're AFK):** install the **ntfy** app from the App Store
  or Play Store → tap **+** → enter the exact topic name → Subscribe.
- **This desktop:** open <https://ntfy.sh> in your browser → subscribe to the
  same topic → click **"Install app"** (or the install icon in the address bar)
  so notifications pop even when the browser is minimized.

The topic name is a shared secret — anyone who knows it can read your alerts, so
don't post it anywhere public.

## 6. Test it before you rely on it

Run the engine in **replay mode**, which pulls real recent market data and works
any time — nights, weekends, holidays. Do this **with your phone next to you**:

```powershell
python -m alertengine --replay
```

You should see the screen table print, then alerts scroll by — and your phone
should buzz with ntfy notifications. If the phone stays silent, the topic
subscription (step 5) is wrong. Fix it now, not at market open.

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
- **No phone alerts** — confirm the topic in the ntfy app exactly matches
  `NTFY_TOPIC` in `.env` (no typos, no extra spaces).
- **Connection / auth error at startup** — `.env` is missing or the Alpaca keys
  are wrong. Check the file is in the project root, not a subfolder.
- **Times look off** — timestamps are shown in Pacific; the `tzdata` package
  (installed in step 3) provides the timezone data Windows needs.
