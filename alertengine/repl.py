"""Console command loop. One long-running process; the watch loop runs as a
background task while the user types commands.

Commands:
  screen                 run the screener, print numbered candidates
  approve <syms...>      add symbols to the approved watchlist
  watchlist              show approved symbols
  watch                  start the 2-min watch loop (background)
  stop                   stop the watch loop
  status                 connection/bars/armed state
  help                   show commands
  quit                   stop and exit
"""

import asyncio
import json

from .engine import AlertEngine

HELP = __doc__.split("Commands:", 1)[1]

MOST_ACTIVE_URL = "https://finance.yahoo.com/markets/stocks/most-active/"


async def _ainput(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)


async def run(engine: AlertEngine) -> None:
    print("Trading alert engine. Type 'help' for commands.")
    watch_task: asyncio.Task | None = None
    last_candidates: list = []

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
            last_candidates = await engine.screen()
            # In replay mode, note the historical date range the bars come from.
            window = getattr(engine.feed, "describe_window", None)
            if window:
                print(window())
            if not last_candidates:
                print("no candidates")
            else:
                # Bare URLs are ⌘-clickable in every Mac terminal (unlike OSC 8
                # hyperlinks, which Terminal.app ignores). Quote page per row.
                print(f"Yahoo most active: {MOST_ACTIVE_URL}\n")
            for i, c in enumerate(last_candidates, 1):
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
                print("watchlist:", ", ".join(engine.gate.watchlist()) or "(empty)")

        elif cmd == "watchlist":
            print(", ".join(engine.gate.watchlist()) or "(empty)")

        elif cmd == "watch":
            if watch_task and not watch_task.done():
                print("already watching")
            else:
                symbols = engine.gate.watchlist()
                if not symbols:
                    print("nothing approved; use 'approve <symbols...>' first")
                else:
                    watch_task = asyncio.create_task(engine.watch(symbols))
                    print(f"watching: {', '.join(symbols)}")

        elif cmd == "stop":
            if watch_task and not watch_task.done():
                watch_task.cancel()
                try:
                    await watch_task
                except asyncio.CancelledError:
                    pass
                print("stopped")
            else:
                print("not watching")

        elif cmd == "status":
            print(json.dumps(engine.status(), indent=4))

        elif cmd == "quit":
            if watch_task and not watch_task.done():
                watch_task.cancel()
                try:
                    await watch_task
                except asyncio.CancelledError:
                    pass
            print("bye")
            return

        else:
            print(f"unknown command: {cmd} (try 'help')")

        # One blank line after each command's output block, so successive
        # commands don't run together. Centralized here instead of appending
        # "\n" to every print above. (quit returns early; empty input continues.)
        print()
