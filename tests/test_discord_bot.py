import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alertengine.discord_bot import DiscordConfig, DiscordBot
from alertengine.models import Alert


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
            self.replaced = False

        async def replace_from_gate(self, start):
            self.replaced = start

    gate = FakeGate()
    controller = FakeController()
    bot = object.__new__(DiscordBot)
    bot.engine = SimpleNamespace(gate=gate)
    bot.controller = controller

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(
        "alertengine.discord_bot.load_candidates", lambda path: ["AAPL", "MSFT"]
    )

    asyncio.run(bot._run_prescreen_job(FakeChannel()))

    assert gate.approved == ["AAPL", "MSFT"]
    assert controller.replaced is True
    assert messages == ["✅ Pre-screen found 2: AAPL, MSFT"]


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
