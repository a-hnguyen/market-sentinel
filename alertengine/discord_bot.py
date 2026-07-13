"""Private Discord control plane and alert notifier.

Slash commands are received over Discord's outbound Gateway websocket, so the
EC2 instance keeps its no-inbound-ports posture. Runtime checks restrict every
command to one guild, one channel, and an explicit user allowlist.
"""

import asyncio
import io
import json
import os
import sys
from dataclasses import dataclass
from datetime import timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from . import settings
from .engine import AlertEngine
from .interfaces import Notifier
from .models import Alert, Candidate
from .notifiers.multi_notifier import MultiNotifier
from .prescreen.sinks import load_candidates
from .watch_controller import WatchController

_PACIFIC = ZoneInfo("America/Los_Angeles")


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


class DiscordBot(discord.Client, Notifier):
    def __init__(
        self,
        engine: AlertEngine,
        controller: WatchController,
        config: DiscordConfig,
    ) -> None:
        super().__init__(intents=discord.Intents.none())
        self.engine = engine
        self.controller = controller
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self._prescreen_task: asyncio.Task[None] | None = None
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
        lines = ["```text"]
        for c in candidates[:20]:
            lines.append(
                f"{c.symbol:<7} ${c.price:>8.2f}  {c.pct_change:>+6.1f}%  "
                f"volx{c.volume_ratio:>4.1f}"
            )
        lines.append("```")
        if len(candidates) > 20:
            lines.append(f"Showing 20 of {len(candidates)} candidates.")
        return "\n".join(lines)

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
            if symbols:
                self.engine.gate.approve(*symbols)
                await self.controller.replace_from_gate(start=True)
            await channel.send(
                f"✅ Pre-screen found {len(symbols)}: {self._symbols(symbols)}"
            )
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except Exception as exc:
            await channel.send(f"❌ Pre-screen failed: {exc}")

    def _register_commands(self) -> None:
        @self.tree.command(name="watch", description="Add a stock and watch it now")
        @app_commands.describe(stock="Ticker symbol, for example AAPL")
        async def watch(interaction: discord.Interaction, stock: str) -> None:
            if not await self._guard(interaction):
                return
            await interaction.response.defer(thinking=True)
            try:
                symbol, symbols = await self.controller.watch(stock)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await interaction.followup.send(
                f"✅ **{symbol}** added and streaming.\nWatchlist: {self._symbols(symbols)}"
            )

        @self.tree.command(name="unwatch", description="Stop watching a stock")
        @app_commands.describe(stock="Ticker symbol to remove")
        async def unwatch(interaction: discord.Interaction, stock: str) -> None:
            if not await self._guard(interaction):
                return
            await interaction.response.defer(thinking=True)
            try:
                symbol, symbols = await self.controller.unwatch(stock)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await interaction.followup.send(
                f"🛑 **{symbol}** removed.\nWatchlist: {self._symbols(symbols)}"
            )

        @self.tree.command(name="watchlist", description="Show watched stocks")
        async def watchlist(interaction: discord.Interaction) -> None:
            if await self._guard(interaction):
                await interaction.response.send_message(
                    f"**Watchlist:** {self._symbols(self.engine.gate.watchlist())}"
                )

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
            name="prescreen", description="Run overnight pre-screen and watch survivors"
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
                await interaction.response.send_message(
                    "**Commands**\n"
                    "`/watch STOCK` · `/unwatch STOCK` · `/watchlist`\n"
                    "`/status [STOCK]` · `/screen` · `/prescreen`\n"
                    "`/start` · `/stop confirm:true`"
                )

    @staticmethod
    def alert_embed(alert: Alert) -> discord.Embed:
        kind = getattr(alert, "kind", "alert")
        colors = {
            "watch": 0xF1C40F,
            "buy": 0x2ECC71,
            "sell_watch": 0xE67E22,
            "sell": 0xE74C3C,
        }
        labels = {
            "watch": "BUY SETUP ARMED",
            "buy": "BUY ALERT",
            "sell_watch": "SELL SETUP ARMED",
            "sell": "SELL ALERT",
        }
        embed = discord.Embed(
            title=f"{labels.get(kind, 'MARKET ALERT')} — {alert.symbol}",
            description=alert.message,
            color=colors.get(kind, 0x3498DB),
        )
        for key, value in (alert.context or {}).items():
            if isinstance(value, float):
                value = f"{value:.2f}"
            embed.add_field(name=key.replace("_", " ").title(), value=str(value))
        ts = alert.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        embed.set_footer(text=ts.astimezone(_PACIFIC).strftime("%Y-%m-%d %H:%M %Z"))
        return embed

    async def send(self, alert: Alert) -> None:
        if not self.is_ready():
            try:
                await asyncio.wait_for(self.wait_until_ready(), timeout=10)
            except asyncio.TimeoutError:
                raise RuntimeError("Discord bot is not ready")
        channel = self.get_channel(self.config.channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.config.channel_id)
        await channel.send(embed=self.alert_embed(alert))


async def run_discord(engine: AlertEngine, auto_approve: bool = True) -> None:
    config = DiscordConfig.from_env()
    controller = WatchController(engine)
    controller.load_manual()
    if auto_approve and os.path.exists(settings.PRESCREEN_OUTPUT_PATH):
        symbols = load_candidates(settings.PRESCREEN_OUTPUT_PATH)
        if symbols:
            engine.gate.approve(*symbols)

    bot = DiscordBot(engine, controller, config)
    engine.notifier = MultiNotifier([engine.notifier, bot])
    if engine.gate.watchlist():
        await controller.start()

    try:
        await bot.start(config.token)
    finally:
        await controller.stop()
