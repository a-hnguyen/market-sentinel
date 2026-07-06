"""Entry point.

  python -m alertengine           # mock mode (no API keys, synthetic bars)
  python -m alertengine --replay  # real historical 1-min bars (works any time)
  python -m alertengine --live    # real-time mode (yfinance screen + Alpaca stream)

Live/replay mode reads ALPACA_API_KEY / ALPACA_SECRET_KEY (from a .env file).
`--replay` pulls a recent trading day's real bars over REST and replays them, so
you can exercise the full pipeline on real data when the market is closed;
`--live` streams real-time bars (only emits during market hours). Because
everything sits behind the four interfaces, switching modes only changes which
Screener/DataFeed get constructed here — the engine is untouched.
"""

import asyncio
import sys

from .engine import AlertEngine
from .gate import ApprovalGate
from .notifiers.console_notifier import ConsoleNotifier
from .repl import run
from .rules.bb_rsi_rule import BBRSIRule


def build_engine(live: bool = False, replay: bool = False) -> AlertEngine:
    if live or replay:
        from dotenv import load_dotenv

        from .screeners.yfinance_screener import YFinanceScreener

        load_dotenv()  # pull ALPACA_* from .env if present
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
        notifier=ConsoleNotifier(),
        gate=ApprovalGate(),
    )


if __name__ == "__main__":
    args = sys.argv[1:]
    live = "--live" in args
    replay = "--replay" in args
    try:
        engine = build_engine(live=live, replay=replay)
    except RuntimeError as e:
        # e.g. missing Alpaca credentials in --live mode.
        print(f"error: {e}")
        print("hint: copy .env.example to .env and add your keys, or omit --live.")
        sys.exit(1)
    try:
        asyncio.run(run(engine))
    except KeyboardInterrupt:
        pass
