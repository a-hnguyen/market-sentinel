import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alertengine.discord_bot import DiscordConfig, DiscordBot
from alertengine.models import Alert


def _alert(kind="watch", *, watcher=None):
    context = {
        "close": 211.42,
        "rsi": 27.8,
        "expires_at": "2026-07-13T16:20:00+00:00",
    }
    if watcher:
        context["watcher"] = watcher
    return Alert(
        symbol="AAPL",
        timestamp=datetime(2026, 7, 13, 16, 0, tzinfo=timezone.utc),
        rule="test",
        message=f"{kind} AAPL",
        context=context,
        kind=kind,
    )


class _FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        message = _FakeMessage()
        self.sent.append((kwargs, message))
        return message


def _bare_bot():
    bot = object.__new__(DiscordBot)
    bot._lifecycle_messages = {}
    return bot


def test_config_requires_all_values(monkeypatch):
    for key in (
        "DISCORD_BOT_TOKEN",
        "DISCORD_GUILD_ID",
        "DISCORD_CHANNEL_ID",
        "DISCORD_ALLOWED_USER_IDS",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="Discord requires"):
        DiscordConfig.from_env()


def test_config_parses_allowlist(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "10")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "20")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "30, 40")
    config = DiscordConfig.from_env()
    assert config.guild_id == 10
    assert config.channel_id == 20
    assert config.allowed_user_ids == {30, 40}


def test_config_rejects_non_positive_ids(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "10")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "0")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "30")
    with pytest.raises(RuntimeError, match="positive"):
        DiscordConfig.from_env()


def test_alert_embed_contains_actionable_context():
    alert = Alert(
        symbol="AAPL",
        timestamp=datetime(2026, 7, 13, 16, 0, tzinfo=timezone.utc),
        rule="bb_rsi_buy",
        message="BUY AAPL",
        context={"close": 211.42, "rsi": 27.8},
        kind="buy",
    )
    embed = DiscordBot.alert_embed(alert)
    assert embed.title == "BUY ALERT — AAPL"
    assert embed.description == "BUY AAPL"
    assert {field.name for field in embed.fields} == {"Close", "Rsi"}


def test_watch_embed_shows_a_localized_relative_expiry():
    embed = DiscordBot.alert_embed(_alert())
    expires = next(field for field in embed.fields if field.name == "Expires At")
    assert expires.value.startswith("<t:")
    assert ":R> · <t:" in expires.value
    assert expires.value.endswith(":t>")


def test_watch_links_prioritize_yahoo_research():
    children = DiscordBot.alert_links(_alert()).children
    assert [button.label for button in children] == [
        "Research on Yahoo",
        "Open in Robinhood",
    ]
    assert children[0].url == "https://finance.yahoo.com/quote/AAPL"
    assert children[1].url == "https://robinhood.com/us/en/stocks/AAPL/"


def test_confirmed_links_prioritize_robinhood():
    children = DiscordBot.alert_links(_alert("buy")).children
    assert [button.label for button in children] == [
        "Open in Robinhood",
        "Research on Yahoo",
    ]


def test_expired_links_remove_the_brokerage_action():
    children = DiscordBot.alert_links(
        _alert("watch_expired"), lifecycle_state="expired"
    ).children
    assert [button.label for button in children] == ["Research on Yahoo"]


def test_screen_candidates_link_to_yahoo():
    candidate = SimpleNamespace(
        symbol="AAPL", price=211.42, pct_change=-10.2, volume_ratio=2.3
    )
    body = DiscordBot._candidate_lines([candidate])
    assert "[AAPL](https://finance.yahoo.com/quote/AAPL)" in body
    assert "```" not in body


def test_confirmation_edits_watch_card_and_sends_fresh_alert():
    bot = _bare_bot()
    channel = _FakeChannel()

    async def drive():
        await bot._deliver_alert(channel, _alert("watch"))
        await bot._deliver_alert(channel, _alert("buy"))

    asyncio.run(drive())

    assert len(channel.sent) == 2
    watch_message = channel.sent[0][1]
    assert len(watch_message.edits) == 1
    edited = watch_message.edits[0]["embed"]
    assert edited.title == "BUY SETUP CONFIRMED — AAPL"
    assert "watch AAPL" in edited.description
    assert "CONFIRMED" in edited.description
    assert {field.name for field in edited.fields} >= {"Rsi", "Final Close"}
    assert channel.sent[1][0]["embed"].title == "BUY ALERT — AAPL"
    assert bot._lifecycle_messages == {}


def test_expiration_edits_watch_card_without_a_new_notification():
    bot = _bare_bot()
    channel = _FakeChannel()

    async def drive():
        await bot._deliver_alert(channel, _alert("watch"))
        await bot._deliver_alert(channel, _alert("watch_expired"))

    asyncio.run(drive())

    assert len(channel.sent) == 1
    watch_message = channel.sent[0][1]
    edited = watch_message.edits[0]["embed"]
    assert edited.title == "BUY SETUP EXPIRED — AAPL"
    assert "watch AAPL" in edited.description
    assert "EXPIRED" in edited.description
    assert [button.label for button in watch_message.edits[0]["view"].children] == [
        "Research on Yahoo"
    ]
    assert bot._lifecycle_messages == {}


def test_sell_lifecycle_uses_its_own_card_and_fresh_confirmation():
    bot = _bare_bot()
    channel = _FakeChannel()

    async def drive():
        await bot._deliver_alert(channel, _alert("sell_watch"))
        await bot._deliver_alert(channel, _alert("sell"))

    asyncio.run(drive())

    assert len(channel.sent) == 2
    edited = channel.sent[0][1].edits[0]["embed"]
    assert edited.title == "SELL SETUP CONFIRMED — AAPL"
    assert channel.sent[1][0]["embed"].title == "SELL ALERT — AAPL"


def test_regular_and_overnight_lifecycle_cards_do_not_collide():
    bot = _bare_bot()
    channel = _FakeChannel()

    async def drive():
        await bot._deliver_alert(channel, _alert("watch"))
        await bot._deliver_alert(channel, _alert("watch", watcher="penny-overnight"))

    asyncio.run(drive())

    assert len(bot._lifecycle_messages) == 2


def test_failed_card_edit_does_not_suppress_fresh_confirmation():
    class BrokenMessage(_FakeMessage):
        async def edit(self, **kwargs):
            raise RuntimeError("message disappeared")

    bot = _bare_bot()
    channel = _FakeChannel()

    asyncio.run(bot._deliver_alert(channel, _alert("watch")))
    card = bot._lifecycle_messages[("regular", "AAPL", "buy")]
    card.message = BrokenMessage()
    channel.sent.clear()

    asyncio.run(bot._deliver_alert(channel, _alert("buy")))

    assert len(channel.sent) == 1
    assert channel.sent[0][0]["embed"].title == "BUY ALERT — AAPL"


def test_premarket_alert_embed_is_clearly_labelled():
    alert = Alert(
        symbol="AAPL",
        # 13:29 UTC in July is 06:29 PDT.
        timestamp=datetime(2026, 7, 13, 13, 29, tzinfo=timezone.utc),
        rule="bb_rsi_buy",
        message="BUY AAPL",
        kind="buy",
    )

    assert DiscordBot.alert_embed(alert).title == "PREMARKET · BUY ALERT — AAPL"


def test_market_open_alert_embed_is_not_labelled_premarket():
    alert = Alert(
        symbol="AAPL",
        # 13:30 UTC in July is exactly 06:30 PDT.
        timestamp=datetime(2026, 7, 13, 13, 30, tzinfo=timezone.utc),
        rule="bb_rsi_buy",
        message="BUY AAPL",
        kind="buy",
    )

    assert DiscordBot.alert_embed(alert).title == "BUY ALERT — AAPL"


def test_prescreen_job_runs_in_subprocess_and_reports_results(monkeypatch):
    messages = []

    class FakeChannel:
        async def send(self, message):
            messages.append(message)

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"2 oversold survivor(s)", b""

    async def fake_subprocess(*args, **kwargs):
        assert args[-1] == "--force"
        assert kwargs["stderr"] == asyncio.subprocess.STDOUT
        return FakeProcess()

    class FakeGate:
        def __init__(self):
            self.approved = []

        def approve(self, *symbols):
            self.approved.extend(symbols)

    class FakeController:
        def __init__(self):
            self.replaced = None

        async def replace_automatic(self, symbols, start):
            self.replaced = (symbols, start)

    gate = FakeGate()
    controller = FakeController()
    bot = object.__new__(DiscordBot)
    bot.engine = SimpleNamespace(gate=gate)
    bot.controller = controller

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(
        "alertengine.discord_bot.load_candidates", lambda path: ["AAPL", "NVDA"]
    )
    monkeypatch.setattr(
        "alertengine.discord_bot.load_report",
        lambda path: {
            "oversold_slow_matches": ["AAPL", "MSFT"],
            "oversold_fast_matches": ["AAPL"],
            "oversold_final": ["AAPL"],
            "overbought_slow_matches": ["NVDA"],
            "overbought_fast_matches": ["NVDA"],
            "overbought_final": ["NVDA"],
            "automatic": ["AAPL", "NVDA"],
            "added": ["MSFT"],
            "removed": ["TSLA"],
        },
    )

    asyncio.run(bot._run_prescreen_job(FakeChannel()))

    assert gate.approved == []
    assert controller.replaced == (["AAPL", "NVDA"], True)
    assert "Removed: TSLA" in messages[0]
    assert "OVERSOLD · 4-hour RSI < 30 (2):** AAPL, MSFT" in messages[1]
    assert "OVERBOUGHT · both (1):** NVDA" in messages[6]
    assert "Automatic watchlist (2):** AAPL, NVDA" in messages[7]


def test_prescreen_job_kills_timed_out_process(monkeypatch):
    messages = []

    class FakeChannel:
        async def send(self, message):
            messages.append(message)

    class FakeProcess:
        returncode = None
        killed = False

        async def communicate(self):
            await asyncio.Future()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = FakeProcess()

    async def fake_subprocess(*args, **kwargs):
        return process

    async def immediate_timeout(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError

    bot = object.__new__(DiscordBot)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)

    asyncio.run(bot._run_prescreen_job(FakeChannel()))

    assert process.killed is True
    assert messages[0].startswith("⏱️ Pre-screen stopped")
