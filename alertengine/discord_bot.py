"""Private Discord control plane and alert notifier.

Slash commands are received over Discord's outbound Gateway websocket, so the
EC2 instance keeps its no-inbound-ports posture. Runtime checks restrict every
command to one guild, one channel, and an explicit user allowlist.
"""

import asyncio
import io
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from . import settings
from .engine import AlertEngine
from .interfaces import Notifier
from .market_session import in_premarket
from .models import Alert, Candidate
from .notifiers.multi_notifier import MultiNotifier
from .notifiers.tagged_notifier import TaggedNotifier
from .prescreen.reporting import load_report
from .prescreen.sinks import load_candidates
from .watch_controller import WatchController

_PACIFIC = ZoneInfo("America/Los_Angeles")
_LOG = logging.getLogger("alertengine.discord")


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    guild_id: int
    channel_id: int
    allowed_user_ids: frozenset[int]

    @classmethod
    def from_env(cls) -> "DiscordConfig":
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        guild = os.environ.get("DISCORD_GUILD_ID", "").strip()
        channel = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
        users = os.environ.get("DISCORD_ALLOWED_USER_IDS", "").strip()
        if not token or not guild or not channel or not users:
            raise RuntimeError(
                "Discord requires DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, "
                "DISCORD_CHANNEL_ID, and DISCORD_ALLOWED_USER_IDS"
            )
        try:
            allowed = frozenset(int(value.strip()) for value in users.split(","))
            guild_id = int(guild)
            channel_id = int(channel)
        except ValueError as exc:
            raise RuntimeError("Discord IDs must be numeric") from exc
        if guild_id <= 0 or channel_id <= 0 or not allowed or min(allowed) <= 0:
            raise RuntimeError("Discord IDs must be positive")
        return cls(token, guild_id, channel_id, allowed)


@dataclass
class _LifecycleCard:
    message: discord.Message
    armed_alert: Alert


class DiscordBot(discord.Client, Notifier):
    def __init__(
        self,
        engine: AlertEngine,
        controller: WatchController,
        config: DiscordConfig,
        penny_engine: AlertEngine | None = None,
        penny_controller: WatchController | None = None,
    ) -> None:
        super().__init__(intents=discord.Intents.none())
        self.engine = engine
        self.controller = controller
        self.config = config
        self.penny_engine = penny_engine
        self.penny_controller = penny_controller
        self.tree = app_commands.CommandTree(self)
        self._prescreen_task: asyncio.Task[None] | None = None
        self._lifecycle_messages: dict[tuple[str, str, str], _LifecycleCard] = {}
        self._register_commands()

    async def setup_hook(self) -> None:
        # Guild commands update immediately, which is preferable for this one
        # private server and avoids globally exposing the command catalog.
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
        print(f"Discord control ready as {self.user}", flush=True)

    def _authorized(self, interaction: discord.Interaction) -> bool:
        return (
            interaction.guild_id == self.config.guild_id
            and interaction.channel_id == self.config.channel_id
            and interaction.user.id in self.config.allowed_user_ids
        )

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if self._authorized(interaction):
            return True
        await interaction.response.send_message(
            "This command is not authorized here.", ephemeral=True
        )
        return False

    @staticmethod
    def _symbols(symbols: list[str]) -> str:
        return ", ".join(symbols) if symbols else "(empty)"

    @staticmethod
    def _candidate_lines(candidates: list[Candidate]) -> str:
        if not candidates:
            return "No candidates."
        lines = []
        for c in candidates[:20]:
            url = DiscordBot.yahoo_url(c.symbol)
            lines.append(
                f"[{c.symbol}]({url}) · `${c.price:>8.2f}` · "
                f"`{c.pct_change:>+6.1f}%` · `volx{c.volume_ratio:>4.1f}`"
            )
        if len(candidates) > 20:
            lines.append(f"Showing 20 of {len(candidates)} candidates.")
        return "\n".join(lines)

    @staticmethod
    def yahoo_url(symbol: str) -> str:
        return f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}"

    @staticmethod
    def robinhood_url(symbol: str) -> str:
        return f"https://robinhood.com/us/en/stocks/{quote(symbol, safe='')}/"

    @staticmethod
    def alert_links(
        alert: Alert, lifecycle_state: str | None = None
    ) -> discord.ui.View:
        """Research first while armed; brokerage first after confirmation."""
        view = discord.ui.View(timeout=None)
        yahoo = discord.ui.Button(
            label="Research on Yahoo",
            url=DiscordBot.yahoo_url(alert.symbol),
        )
        robinhood = discord.ui.Button(
            label="Open in Robinhood",
            url=DiscordBot.robinhood_url(alert.symbol),
        )
        if lifecycle_state == "expired":
            view.add_item(yahoo)
        elif lifecycle_state == "confirmed" or alert.kind in ("buy", "sell"):
            view.add_item(robinhood)
            view.add_item(yahoo)
        else:
            view.add_item(yahoo)
            view.add_item(robinhood)
        return view

    async def _run_prescreen_job(self, channel: discord.abc.Messageable) -> None:
        """Run the CPU-heavy scan out of process and report back to Discord."""
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "alertengine.prescreen",
                "--force",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(
                    process.communicate(), timeout=settings.PRESCREEN_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                await channel.send(
                    "⏱️ Pre-screen stopped after "
                    f"{settings.PRESCREEN_TIMEOUT_SECONDS // 60} minutes. "
                    "The live watcher and Discord bot are still running."
                )
                return

            text = output.decode("utf-8", errors="replace").strip()
            if process.returncode != 0:
                detail = text[-1500:] or f"process exited {process.returncode}"
                await channel.send(f"❌ Pre-screen failed:\n```text\n{detail}\n```")
                return

            symbols = load_candidates(settings.PRESCREEN_OUTPUT_PATH)
            await self.controller.replace_automatic(symbols, start=True)
            report = load_report(settings.PRESCREEN_REPORT_PATH)
            await channel.send(
                "✅ **Pre-screen complete (regular session only)**\n"
                f"Added: {self._symbols(report['added'])}\n"
                f"Removed: {self._symbols(report['removed'])}"
            )
            await channel.send(
                f"**OVERSOLD · 4-hour RSI < 30 "
                f"({len(report['oversold_slow_matches'])}):** "
                f"{self._symbols(report['oversold_slow_matches'])}"
            )
            await channel.send(
                f"**OVERSOLD · 1-hour RSI < 30 "
                f"({len(report['oversold_fast_matches'])}):** "
                f"{self._symbols(report['oversold_fast_matches'])}"
            )
            await channel.send(
                f"**OVERSOLD · both ({len(report['oversold_final'])}):** "
                f"{self._symbols(report['oversold_final'])}"
            )
            await channel.send(
                f"**OVERBOUGHT · 4-hour RSI > 70 "
                f"({len(report['overbought_slow_matches'])}):** "
                f"{self._symbols(report['overbought_slow_matches'])}"
            )
            await channel.send(
                f"**OVERBOUGHT · 1-hour RSI > 70 "
                f"({len(report['overbought_fast_matches'])}):** "
                f"{self._symbols(report['overbought_fast_matches'])}"
            )
            await channel.send(
                f"**OVERBOUGHT · both ({len(report['overbought_final'])}):** "
                f"{self._symbols(report['overbought_final'])}"
            )
            await channel.send(
                f"**Automatic watchlist ({len(report['automatic'])}):** "
                f"{self._symbols(report['automatic'])}"
            )
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except Exception as exc:
            await channel.send(f"❌ Pre-screen failed: {exc}")

    def _register_commands(self) -> None:
        @self.tree.command(name="watch", description="Add stocks and watch them now")
        @app_commands.describe(stocks="Space-separated tickers, for example AAPL MSFT")
        async def watch(interaction: discord.Interaction, stocks: str) -> None:
            if not await self._guard(interaction):
                return
            await interaction.response.defer(thinking=True)
            try:
                added, invalid, symbols = await self.controller.watch_many(stocks)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            lines = []
            if added:
                lines.append(f"✅ Added and streaming: **{self._symbols(added)}**")
            if invalid:
                lines.append(f"⚠️ Skipped invalid: `{self._symbols(invalid)}`")
            if self.controller.persistence_status["last_error"]:
                lines.append("⚠️ Saved locally, but S3 persistence failed.")
            lines.append(f"Watchlist: {self._symbols(symbols)}")
            await interaction.followup.send("\n".join(lines))

        @self.tree.command(name="unwatch", description="Stop watching stocks")
        @app_commands.describe(stocks="Space-separated tickers to remove")
        async def unwatch(interaction: discord.Interaction, stocks: str) -> None:
            if not await self._guard(interaction):
                return
            await interaction.response.defer(thinking=True)
            try:
                removed, invalid, symbols = await self.controller.unwatch_many(stocks)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            lines = []
            if removed:
                lines.append(f"🛑 Removed: **{self._symbols(removed)}**")
            if invalid:
                lines.append(f"⚠️ Skipped invalid: `{self._symbols(invalid)}`")
            if self.controller.persistence_status["last_error"]:
                lines.append("⚠️ Saved locally, but S3 persistence failed.")
            lines.append(f"Watchlist: {self._symbols(symbols)}")
            await interaction.followup.send("\n".join(lines))

        @self.tree.command(name="watchlist", description="Show watched stocks")
        async def watchlist(interaction: discord.Interaction) -> None:
            if await self._guard(interaction):
                await interaction.response.send_message(
                    f"**Watchlist:** {self._symbols(self.engine.gate.watchlist())}"
                )

        @self.tree.command(name="strategy", description="Show or switch alert strategy")
        @app_commands.describe(name="Strategy name; omit to list available choices")
        async def strategy(
            interaction: discord.Interaction, name: str | None = None
        ) -> None:
            if not await self._guard(interaction):
                return
            if name is None:
                available = ", ".join(self.engine.available_strategies)
                await interaction.response.send_message(
                    f"Active strategy: **{self.engine.strategy_name}**\n"
                    f"Available: {available}"
                )
                return
            await interaction.response.defer(thinking=True)
            try:
                active, changed = await self.controller.select_strategy(name)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            action = "Switched to" if changed else "Already using"
            message = f"✅ {action} **{active}**."
            if self.controller.strategy_persistence_status["last_error"]:
                message += "\n⚠️ Saved locally, but S3 persistence failed."
            await interaction.followup.send(message)

        @self.tree.command(name="start", description="Start the market watcher")
        async def start(interaction: discord.Interaction) -> None:
            if not await self._guard(interaction):
                return
            try:
                symbols = await self.controller.start()
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            await interaction.response.send_message(
                f"▶️ Watching {self._symbols(symbols)}"
            )

        @self.tree.command(name="stop", description="Stop all market streaming")
        @app_commands.describe(confirm="Must be true to stop the watcher")
        async def stop(interaction: discord.Interaction, confirm: bool = False) -> None:
            if not await self._guard(interaction):
                return
            if not confirm:
                await interaction.response.send_message(
                    "Run `/stop confirm:true` to stop all streaming.", ephemeral=True
                )
                return
            await interaction.response.defer(thinking=True)
            await self.controller.stop()
            await interaction.followup.send("⏹️ Market watcher stopped.")

        @self.tree.command(name="status", description="Show engine status")
        @app_commands.describe(stock="Optional ticker to inspect")
        async def status(
            interaction: discord.Interaction, stock: str | None = None
        ) -> None:
            if not await self._guard(interaction):
                return
            status_data = self.engine.status()
            status_data["controller_running"] = self.controller.running
            status_data["active_symbols"] = self.controller.active_symbols
            status_data["watchlist"] = self.engine.gate.watchlist()
            status_data["automatic_symbols"] = self.controller.automatic_symbols
            status_data["manual_symbols"] = self.controller.manual_symbols
            status_data["manual_persistence"] = self.controller.persistence_status
            status_data["strategy_persistence"] = (
                self.controller.strategy_persistence_status
            )
            if stock:
                try:
                    symbol = self.controller.normalize(stock)
                except ValueError as exc:
                    await interaction.response.send_message(str(exc), ephemeral=True)
                    return
                status_data["symbols"] = {
                    symbol: status_data["symbols"].get(symbol, "no bars yet")
                }
            body = json.dumps(status_data, indent=2)
            if len(body) <= 1850:
                await interaction.response.send_message(f"```json\n{body}\n```")
            else:
                payload = io.BytesIO(body.encode("utf-8"))
                await interaction.response.send_message(
                    "Status is attached because it exceeds Discord's message limit.",
                    file=discord.File(payload, filename="market-sentinel-status.json"),
                )

        @self.tree.command(name="screen", description="Run the live stock screener")
        async def screen(interaction: discord.Interaction) -> None:
            if not await self._guard(interaction):
                return
            await interaction.response.defer(thinking=True)
            try:
                candidates = await self.engine.screen()
                await interaction.followup.send(self._candidate_lines(candidates))
            except Exception as exc:
                await interaction.followup.send(f"Screen failed: {exc}", ephemeral=True)

        @self.tree.command(
            name="prescreen",
            description="Run post-close pre-screen and replace automatic candidates",
        )
        async def prescreen(interaction: discord.Interaction) -> None:
            if not await self._guard(interaction):
                return
            if self._prescreen_task is not None and not self._prescreen_task.done():
                await interaction.response.send_message(
                    "A pre-screen is already running. I’ll post here when it finishes.",
                    ephemeral=True,
                )
                return
            channel = interaction.channel
            if channel is None:
                channel = await self.fetch_channel(self.config.channel_id)
            self._prescreen_task = asyncio.create_task(
                self._run_prescreen_job(channel), name="discord-prescreen"
            )
            await interaction.response.send_message(
                "🔄 Pre-screen started in the background. I’ll post the result here."
            )

        @self.tree.command(name="help", description="Show market-sentinel commands")
        async def help_command(interaction: discord.Interaction) -> None:
            if await self._guard(interaction):
                penny = ""
                if self.penny_controller is not None:
                    penny = (
                        "\n`/penny-watch STOCKS` · `/penny-unwatch STOCKS` · "
                        "`/penny-watchlist`\n"
                        "`/penny-status [STOCK]` · `/penny-start` · "
                        "`/penny-stop confirm:true`"
                    )
                await interaction.response.send_message(
                    "**Commands**\n"
                    "`/watch STOCKS` · `/unwatch STOCKS` · `/watchlist`\n"
                    "`/status [STOCK]` · `/strategy [NAME]` · `/screen` · `/prescreen`\n"
                    "`/start` · `/stop confirm:true`" + penny
                )

        if self.penny_engine is not None and self.penny_controller is not None:
            self._register_penny_commands()

    def _register_penny_commands(self) -> None:
        engine = self.penny_engine
        controller = self.penny_controller
        if engine is None or controller is None:
            return

        @self.tree.command(
            name="penny-watch", description="Add stocks to the overnight watcher"
        )
        @app_commands.describe(stocks="Space-separated tickers, for example AMC CELZ")
        async def penny_watch(interaction: discord.Interaction, stocks: str) -> None:
            if not await self._guard(interaction):
                return
            await interaction.response.defer(thinking=True)
            try:
                added, invalid, symbols = await controller.watch_many(stocks)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            lines = []
            if added:
                lines.append(f"✅ Penny watcher added: **{self._symbols(added)}**")
            if invalid:
                lines.append(f"⚠️ Skipped invalid: `{self._symbols(invalid)}`")
            if controller.persistence_status["last_error"]:
                lines.append("⚠️ Saved locally, but S3 persistence failed.")
            lines.append(f"Penny watchlist: {self._symbols(symbols)}")
            await interaction.followup.send("\n".join(lines))

        @self.tree.command(
            name="penny-unwatch", description="Remove stocks from the overnight watcher"
        )
        @app_commands.describe(stocks="Space-separated tickers to remove")
        async def penny_unwatch(interaction: discord.Interaction, stocks: str) -> None:
            if not await self._guard(interaction):
                return
            await interaction.response.defer(thinking=True)
            try:
                removed, invalid, symbols = await controller.unwatch_many(stocks)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            lines = []
            if removed:
                lines.append(f"🛑 Penny watcher removed: **{self._symbols(removed)}**")
            if invalid:
                lines.append(f"⚠️ Skipped invalid: `{self._symbols(invalid)}`")
            if controller.persistence_status["last_error"]:
                lines.append("⚠️ Saved locally, but S3 persistence failed.")
            lines.append(f"Penny watchlist: {self._symbols(symbols)}")
            await interaction.followup.send("\n".join(lines))

        @self.tree.command(
            name="penny-watchlist", description="Show overnight watched stocks"
        )
        async def penny_watchlist(interaction: discord.Interaction) -> None:
            if await self._guard(interaction):
                await interaction.response.send_message(
                    f"**Penny watchlist:** {self._symbols(engine.gate.watchlist())}"
                )

        @self.tree.command(name="penny-start", description="Start overnight watcher")
        async def penny_start(interaction: discord.Interaction) -> None:
            if not await self._guard(interaction):
                return
            try:
                symbols = await controller.start()
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            await interaction.response.send_message(
                f"▶️ Penny watcher streaming {self._symbols(symbols)}"
            )

        @self.tree.command(name="penny-stop", description="Stop overnight watcher")
        @app_commands.describe(confirm="Must be true to stop the watcher")
        async def penny_stop(
            interaction: discord.Interaction, confirm: bool = False
        ) -> None:
            if not await self._guard(interaction):
                return
            if not confirm:
                await interaction.response.send_message(
                    "Run `/penny-stop confirm:true` to stop it.", ephemeral=True
                )
                return
            await interaction.response.defer(thinking=True)
            await controller.stop()
            await interaction.followup.send("⏹️ Penny watcher stopped.")

        @self.tree.command(name="penny-status", description="Show overnight status")
        @app_commands.describe(stock="Optional ticker to inspect")
        async def penny_status(
            interaction: discord.Interaction, stock: str | None = None
        ) -> None:
            if not await self._guard(interaction):
                return
            data = engine.status()
            data["controller_running"] = controller.running
            data["active_symbols"] = controller.active_symbols
            data["watchlist"] = engine.gate.watchlist()
            data["manual_symbols"] = controller.manual_symbols
            data["manual_persistence"] = controller.persistence_status
            if stock:
                try:
                    symbol = controller.normalize(stock)
                except ValueError as exc:
                    await interaction.response.send_message(str(exc), ephemeral=True)
                    return
                data["symbols"] = {symbol: data["symbols"].get(symbol, "no bars yet")}
            await interaction.response.send_message(
                f"```json\n{json.dumps(data, indent=2)[:1850]}\n```"
            )

    @staticmethod
    def alert_embed(alert: Alert) -> discord.Embed:
        kind = getattr(alert, "kind", "alert")
        colors = {
            "watch": 0xF1C40F,
            "buy": 0x2ECC71,
            "sell_watch": 0xE67E22,
            "sell": 0xE74C3C,
            "watch_expired": 0x95A5A6,
            "sell_watch_expired": 0x95A5A6,
        }
        labels = {
            "watch": "BUY SETUP ARMED",
            "buy": "BUY ALERT",
            "sell_watch": "SELL SETUP ARMED",
            "sell": "SELL ALERT",
            "watch_expired": "BUY SETUP EXPIRED",
            "sell_watch_expired": "SELL SETUP EXPIRED",
        }
        session = "PREMARKET · " if in_premarket(alert.timestamp) else ""
        embed = discord.Embed(
            title=f"{session}{labels.get(kind, 'MARKET ALERT')} — {alert.symbol}",
            description=alert.message,
            color=colors.get(kind, 0x3498DB),
        )
        for key, value in (alert.context or {}).items():
            if key == "expires_at":
                try:
                    expires = datetime.fromisoformat(str(value))
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    unix = int(expires.timestamp())
                    value = f"<t:{unix}:R> · <t:{unix}:t>"
                except ValueError:
                    pass
            if isinstance(value, float):
                value = f"{value:.2f}"
            embed.add_field(name=key.replace("_", " ").title(), value=str(value))
        ts = alert.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        embed.set_footer(text=ts.astimezone(_PACIFIC).strftime("%Y-%m-%d %H:%M %Z"))
        return embed

    @staticmethod
    def lifecycle_embed(
        armed_alert: Alert, state: str, outcome: Alert | None = None
    ) -> discord.Embed:
        direction = (
            "BUY" if armed_alert.kind in ("watch", "watch_expired", "buy") else "SELL"
        )
        labels = {"confirmed": "CONFIRMED", "expired": "EXPIRED"}
        colors = {"confirmed": 0x2ECC71, "expired": 0x95A5A6}
        session = "PREMARKET · " if in_premarket(armed_alert.timestamp) else ""
        embed = DiscordBot.alert_embed(armed_alert)
        embed.title = (
            f"{session}{direction} SETUP {labels[state]} — {armed_alert.symbol}"
        )
        embed.color = discord.Color(colors[state])
        if outcome is not None:
            marker = "✅" if state == "confirmed" else "⌛"
            embed.description = (
                f"{armed_alert.message}\n\n{marker} **{labels[state]}:** "
                f"{outcome.message}"
            )
            if "close" in (outcome.context or {}):
                embed.add_field(
                    name="Final Close",
                    value=f"{outcome.context['close']:.2f}",
                )
        return embed

    @staticmethod
    def _lifecycle_key(alert: Alert) -> tuple[str, str, str]:
        watcher = str((alert.context or {}).get("watcher", "regular"))
        direction = "buy" if alert.kind in ("watch", "watch_expired", "buy") else "sell"
        return watcher, alert.symbol.upper(), direction

    async def _deliver_alert(
        self, channel: discord.abc.Messageable, alert: Alert
    ) -> None:
        """Deliver one lifecycle event without coupling the engine to Discord."""
        kind = getattr(alert, "kind", "alert")
        key = self._lifecycle_key(alert)

        if kind in ("watch", "sell_watch"):
            previous = self._lifecycle_messages.pop(key, None)
            if previous is not None:
                try:
                    await previous.message.edit(
                        embed=self.lifecycle_embed(previous.armed_alert, "expired"),
                        view=self.alert_links(
                            previous.armed_alert, lifecycle_state="expired"
                        ),
                    )
                except Exception as exc:
                    _LOG.warning("failed to retire previous lifecycle card: %s", exc)
            message = await channel.send(
                embed=self.alert_embed(alert), view=self.alert_links(alert)
            )
            self._lifecycle_messages[key] = _LifecycleCard(message, alert)
            return

        if kind in ("watch_expired", "sell_watch_expired"):
            card = self._lifecycle_messages.pop(key, None)
            if card is not None:
                try:
                    await card.message.edit(
                        embed=self.lifecycle_embed(card.armed_alert, "expired", alert),
                        view=self.alert_links(alert, lifecycle_state="expired"),
                    )
                except Exception as exc:
                    _LOG.warning("failed to expire lifecycle card: %s", exc)
            return

        if kind in ("buy", "sell"):
            card = self._lifecycle_messages.pop(key, None)
            if card is not None:
                try:
                    await card.message.edit(
                        embed=self.lifecycle_embed(
                            card.armed_alert, "confirmed", alert
                        ),
                        view=self.alert_links(alert, lifecycle_state="confirmed"),
                    )
                except Exception as exc:
                    # A card edit must never suppress the fresh actionable push.
                    _LOG.warning("failed to confirm lifecycle card: %s", exc)

        await channel.send(embed=self.alert_embed(alert), view=self.alert_links(alert))

    async def send(self, alert: Alert) -> None:
        if not self.is_ready():
            try:
                await asyncio.wait_for(self.wait_until_ready(), timeout=10)
            except asyncio.TimeoutError:
                raise RuntimeError("Discord bot is not ready")
        channel = self.get_channel(self.config.channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.config.channel_id)
        await self._deliver_alert(channel, alert)


async def run_discord(
    engine: AlertEngine,
    auto_approve: bool = True,
    penny_engine: AlertEngine | None = None,
) -> None:
    config = DiscordConfig.from_env()
    controller = WatchController(engine)
    controller.load_manual()
    controller.load_strategy()
    if auto_approve and os.path.exists(settings.PRESCREEN_OUTPUT_PATH):
        symbols = load_candidates(settings.PRESCREEN_OUTPUT_PATH)
        controller.load_automatic(symbols)

    penny_controller = None
    if penny_engine is not None:
        penny_controller = WatchController(
            penny_engine,
            manual_watchlist_path=settings.PENNY_WATCHLIST_PATH,
            manual_s3_uri=os.environ.get("PENNY_WATCHLIST_S3_URI", ""),
            task_name="penny-market-watch",
            log_name="alertengine.penny_watch",
        )
        penny_controller.load_manual()

    bot = DiscordBot(
        engine,
        controller,
        config,
        penny_engine=penny_engine,
        penny_controller=penny_controller,
    )
    engine.notifier = MultiNotifier([engine.notifier, bot])
    if penny_engine is not None:
        penny_engine.notifier = TaggedNotifier(
            MultiNotifier([penny_engine.notifier, bot]), "penny-overnight"
        )
    if engine.gate.watchlist():
        await controller.start()
    if penny_controller is not None and penny_engine.gate.watchlist():
        await penny_controller.start()

    try:
        await bot.start(config.token)
    finally:
        await controller.stop()
        if penny_controller is not None:
            await penny_controller.stop()
