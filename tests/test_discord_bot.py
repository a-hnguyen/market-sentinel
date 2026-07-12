from datetime import datetime, timezone

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
