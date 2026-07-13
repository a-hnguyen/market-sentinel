"""Console command loop. One long-running process; the watch loop runs as a
background task while the user types commands.

Commands:
  screen                 run the screener, print numbered candidates
  approve <syms...>      add symbols to the approved watchlist
  prescreen              re-run the overnight pre-screen now, approve survivors
  load [path]            approve tickers from the pre-screen's candidates CSV
  watchlist              show approved symbols
  watch                  start the 2-min watch loop (background)
  stop                   stop the watch loop
  status                 connection/bars/armed state
  help                   show commands
  quit                   stop and exit
"""

import asyncio
import json
import os

from . import settings
from .engine import AlertEngine
from .prescreen.sinks import load_candidates
from .watch_controller import WatchController

HELP = __doc__.split("Commands:", 1)[1]

MOST_ACTIVE_URL = "https://finance.yahoo.com/markets/stocks/most-active/"


async def _ainput(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


def _approve_from_file(engine: AlertEngine, path: str) -> list[str]:
    """Approve the pre-screen's survivors from a candidates CSV into the gate."""
    try:
        symbols = load_candidates(path)
    except FileNotFoundError:
        print(f"no candidates file at {path!r}; run 'python -m alertengine.prescreen'")
        return []
    if not symbols:
        print(f"{path!r} has no tickers")
        return []
    engine.gate.approve(*symbols)
    print(f"approved {len(symbols)} from {path!r}: {', '.join(symbols)}")
    return symbols


async def run(engine: AlertEngine, auto_approve: bool = False) -> None:
    print("Trading alert engine. Type 'help' for commands.")
    controller = WatchController(engine)

    # Auto-approve the overnight pre-screen's survivors on startup, if present,
    # so the watchlist is pre-seeded without any manual 'approve' typing. Only in
    # real-data modes (--live/--replay); mock mode stays a clean sandbox instead
    # of silently seeding real tickers from a leftover candidates CSV.
    if auto_approve and os.path.exists(settings.PRESCREEN_OUTPUT_PATH):
        _approve_from_file(engine, settings.PRESCREEN_OUTPUT_PATH)
        print()

    while True:
        try:
            raw = (await _ainput("alertengine> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raw = "quit"

        if not raw:
            continue
        cmd, *args = raw.split()
        cmd = cmd.lower()

        if cmd == "help":
            print(HELP.strip("\n"))

        elif cmd == "screen":
            candidates = await engine.screen()
            # In replay mode, note the historical date range the bars come from.
            window = getattr(engine.feed, "describe_window", None)
            if window:
                print(window())
            if not candidates:
                print("no candidates")
            else:
                # Bare URLs are ⌘-clickable in every Mac terminal (unlike OSC 8
                # hyperlinks, which Terminal.app ignores). Quote page per row.
                print(f"Yahoo most active: {MOST_ACTIVE_URL}\n")
            for i, c in enumerate(candidates, 1):
                url = f"https://finance.yahoo.com/quote/{c.symbol}"
                # Right-align the numeric fields to fixed widths so every column
                # (and the trailing URL) lines up regardless of value magnitude.
                print(
                    f"{i:>2}.   {c.symbol:<6}   ${c.price:>8.2f}   "
                    f"{c.pct_change:>+6.1f}%   volx{c.volume_ratio:>4.1f}   "
                    f"cap {c.market_cap/1e9:>6.1f}B   [{c.source:<12}]   {url}"
                )

        elif cmd == "approve":
            if not args:
                print("usage: approve <symbols...>")
            else:
                engine.gate.approve(*args)
                if controller.running:
                    await controller.replace_from_gate(start=True)
                print("watchlist:", ", ".join(engine.gate.watchlist()) or "(empty)")

        elif cmd == "prescreen":
            from .prescreen.runner import run_prescreen

            try:
                results = run_prescreen()
            except FileNotFoundError:
                print(
                    "watchlist not found; add the curated .xls/.csv "
                    f"at {settings.PRESCREEN_WATCHLIST_PATH!r}"
                )
            except RuntimeError as e:  # needs Alpaca creds
                print(f"pre-screen needs Alpaca credentials: {e}")
            else:
                syms = [r.symbol for r in results]
                if syms:
                    engine.gate.approve(*syms)
                    if controller.running:
                        await controller.replace_from_gate(start=True)
                print(f"pre-screen approved {len(syms)}: {', '.join(syms) or '(none)'}")

        elif cmd == "load":
            loaded = _approve_from_file(
                engine, args[0] if args else settings.PRESCREEN_OUTPUT_PATH
            )
            if loaded and controller.running:
                await controller.replace_from_gate(start=True)

        elif cmd == "watchlist":
            print(", ".join(engine.gate.watchlist()) or "(empty)")

        elif cmd == "watch":
            if controller.running:
                print("already watching")
            else:
                try:
                    symbols = await controller.start()
                except ValueError:
                    print("nothing approved; use 'approve <symbols...>' first")
                else:
                    print(f"watching: {', '.join(symbols)}")

        elif cmd == "stop":
            if controller.running:
                await controller.stop()
                print("stopped")
            else:
                print("not watching")

        elif cmd == "status":
            print(json.dumps(engine.status(), indent=4))

        elif cmd == "quit":
            await controller.stop()
            print("bye")
            return

        else:
            print(f"unknown command: {cmd} (try 'help')")

        # One blank line after each command's output block, so successive
        # commands don't run together. Centralized here instead of appending
        # "\n" to every print above. (quit returns early; empty input continues.)
        print()
