"""Entry point.

  python -m alertengine           # mock mode (no API keys, synthetic bars)
  python -m alertengine --live    # real mode (yfinance screen + Alpaca 1-min feed)

Live mode reads ALPACA_API_KEY / ALPACA_SECRET_KEY (from a .env file). Because
everything sits behind the four interfaces, switching mock<->live only changes
which Screener/DataFeed get constructed here — the engine is untouched.
"""

import asyncio
import sys

from .engine import AlertEngine
from .gate import ApprovalGate
from .notifiers.console_notifier import ConsoleNotifier
from .repl import run
from .rules.bb_rsi_rule import BBRSIRule


def build_engine(live: bool = False) -> AlertEngine:
    if live:
        from dotenv import load_dotenv

        from .feeds.alpaca_feed import AlpacaFeed
        from .screeners.yfinance_screener import YFinanceScreener

        load_dotenv()  # pull ALPACA_* from .env if present
        screener = YFinanceScreener()
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
    live = "--live" in sys.argv[1:]
    try:
        engine = build_engine(live=live)
    except RuntimeError as e:
        # e.g. missing Alpaca credentials in --live mode.
        print(f"error: {e}")
        print("hint: copy .env.example to .env and add your keys, or omit --live.")
        sys.exit(1)
    try:
        asyncio.run(run(engine))
    except KeyboardInterrupt:
        pass
