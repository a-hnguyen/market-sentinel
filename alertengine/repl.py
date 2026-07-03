"""Console command loop. One long-running process; the watch loop runs as a
background task while the user types commands.

Commands:
  screen              run the screener, print numbered candidates
  approve <syms...>   add symbols to the approved watchlist
  watchlist           show approved symbols
  watch               start the 2-min watch loop (background)
  stop                stop the watch loop
  status              connection/bars/armed state
  help                show commands
  quit                stop and exit
"""

import asyncio

from .engine import AlertEngine

HELP = __doc__.split("Commands:", 1)[1]


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
            print(HELP)

        elif cmd == "screen":
            last_candidates = await engine.screen()
            if not last_candidates:
                print("no candidates")
            for i, c in enumerate(last_candidates, 1):
                print(
                    f"{i:>2}. {c.symbol:<6} ${c.price:<7.2f} "
                    f"{c.pct_change:+.1f}%  volx{c.volume_ratio:.1f}  "
                    f"cap {c.market_cap/1e9:.1f}B  [{c.source}]"
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
            print(engine.status())

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
