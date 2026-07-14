"""Entry point.

  python -m alertengine           # mock mode (no API keys, synthetic bars)
  python -m alertengine --replay  # real historical 1-min bars (works any time)
  python -m alertengine --live    # real-time mode (yfinance screen + Alpaca stream)

Add --prescreen (with --live/--replay) to refresh the overnight candidates by
running the pre-screen at startup before the REPL; the survivors are then
auto-approved into the watchlist. Without it, startup loads the candidates CSV
written by a prior manual or scheduled run.

Live/replay mode reads ALPACA_API_KEY / ALPACA_SECRET_KEY (from a .env file).
`--replay` pulls a recent trading day's real bars over REST and replays them, so
you can exercise the full pipeline on real data when the market is closed;
`--live` streams real-time bars (only emits during market hours). Because
everything sits behind the four interfaces, switching modes only changes which
Screener/DataFeed get constructed here — the engine is untouched.
"""

import asyncio
import sys

from dotenv import load_dotenv

from . import settings
from .engine import AlertEngine
from .gate import ApprovalGate
from .notifiers.console_notifier import ConsoleNotifier
from .repl import run
from .rules.bb_rsi_exit_rule import BBRSIExitRule
from .rules.bb_rsi_rule import BBRSIRule


def build_engine(live: bool = False, replay: bool = False) -> AlertEngine:
    load_dotenv()  # pull ALPACA_* / DISCORD_* from .env if present
    if live or replay:
        from .screeners.yfinance_screener import YFinanceScreener

        screener = YFinanceScreener()
        if replay:
            from .feeds.alpaca_replay_feed import AlpacaReplayFeed

            # Small interval so replayed bars stream visibly in the REPL.
            feed = AlpacaReplayFeed(interval=0.02)  # raises if creds missing
        else:
            from .feeds.alpaca_feed import AlpacaFeed

            feed = AlpacaFeed()  # raises if credentials are missing
    else:
        from .feeds.mock_feed import MockFeed
        from .screeners.mock_screener import MockScreener

        screener = MockScreener()
        # Small interval so bars stream visibly in the REPL rather than instantly.
        feed = MockFeed(symbols=["MOCK", "TESTA"], interval=0.2)

    return AlertEngine(
        screener=screener,
        feed=feed,
        rule=BBRSIRule(),
        exit_rule=BBRSIExitRule(),  # SELL side: overbought -> two red closes
        notifier=ConsoleNotifier(),
        gate=ApprovalGate(),
        buy_confirmation_rule=getattr(settings, "BUY_CONFIRMATION_RULE", None),
    )


if __name__ == "__main__":
    args = sys.argv[1:]
    live = "--live" in args
    replay = "--replay" in args
    headless = "--headless" in args  # no console (server/systemd); auto-watch
    try:
        engine = build_engine(live=live, replay=replay)
    except RuntimeError as e:
        # e.g. missing Alpaca credentials in --live mode.
        print(f"error: {e}")
        print("hint: copy .env.example to .env and add your keys, or omit --live.")
        sys.exit(1)

    if "--prescreen" in args:
        # Refresh candidates before the REPL starts; run()'s auto-approve then
        # picks up the freshly-written CSV. Non-fatal if it can't run.
        from .prescreen.runner import run_prescreen

        try:
            results = run_prescreen()
            print(
                f"pre-screen: {len(results)} survivor(s) -> "
                f"{settings.PRESCREEN_OUTPUT_PATH!r}"
            )
        except (FileNotFoundError, RuntimeError) as e:
            print(f"pre-screen skipped: {e}")

    try:
        if headless:
            # Server/systemd: Discord is the remote REPL and alert channel.
            from .discord_bot import run_discord

            asyncio.run(run_discord(engine, auto_approve=live or replay))
        else:
            # Auto-approve the pre-screen's survivors on startup only in real-data
            # modes; mock mode stays a clean sandbox.
            asyncio.run(run(engine, auto_approve=live or replay))
    except KeyboardInterrupt:
        pass
