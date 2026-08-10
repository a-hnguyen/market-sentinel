"""Authenticated, read-only transport for Robinhood's remote MCP server.

Only ``get_equity_historicals`` is exposed to the rest of the application. The
OAuth credential may authorize more Robinhood capabilities, but this adapter
does not provide a generic tool-call escape hatch or any order method.
"""

import asyncio
import json
import logging
import os
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

DEFAULT_ROBINHOOD_MCP_URL = "https://agent.robinhood.com/mcp/trading"
_ALLOWED_TOOL = "get_equity_historicals"


class FileTokenStorage(TokenStorage):
    """Persist refreshable OAuth state atomically, optionally mirroring to S3."""

    def __init__(self, path: str | Path, s3_uri: str = "") -> None:
        self.path = Path(path)
        self.s3_uri = s3_uri.strip()
        self._lock = asyncio.Lock()
        self._log = logging.getLogger("alertengine.robinhood.auth")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    async def _write_section(self, name: str, value: Mapping[str, Any]) -> None:
        async with self._lock:
            payload = await asyncio.to_thread(self._read)
            payload[name] = dict(value)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
            if self.s3_uri:
                await self._upload()

    async def _upload(self) -> None:
        command = ["aws", "s3", "cp", str(self.path), self.s3_uri]
        region = os.environ.get("AWS_REGION", "").strip()
        if region:
            command.extend(["--region", region])
        try:
            await asyncio.to_thread(
                subprocess.run,
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            self._log.exception("OAuth state saved locally but S3 upload failed")

    async def get_tokens(self) -> OAuthToken | None:
        payload = await asyncio.to_thread(self._read)
        value = payload.get("tokens")
        return OAuthToken.model_validate(value) if value else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self._write_section("tokens", tokens.model_dump(mode="json"))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        payload = await asyncio.to_thread(self._read)
        value = payload.get("client_info")
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self._write_section(
            "client_info", client_info.model_dump(mode="json", exclude_none=True)
        )


def oauth_provider(
    storage: TokenStorage,
    *,
    server_url: str = DEFAULT_ROBINHOOD_MCP_URL,
    redirect_handler=None,
    callback_handler=None,
) -> OAuthClientProvider:
    metadata = OAuthClientMetadata(
        client_name="Market Sentinel",
        redirect_uris=["http://127.0.0.1:54069/callback"],
        scope="internal",
    )
    return OAuthClientProvider(
        server_url,
        metadata,
        storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


class RobinhoodMCPTransport:
    """Make a single allowlisted Robinhood MCP request per method call."""

    def __init__(
        self,
        storage: TokenStorage,
        *,
        server_url: str = DEFAULT_ROBINHOOD_MCP_URL,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._storage = storage
        self._server_url = server_url
        self._timeout = timeout_seconds

    async def get_equity_historicals(
        self, request: Mapping[str, object]
    ) -> Mapping[str, Any]:
        provider = oauth_provider(self._storage, server_url=self._server_url)
        async with httpx.AsyncClient(auth=provider, timeout=self._timeout) as http:
            # Robinhood's stateless endpoint rejects MCP DELETE teardown with
            # 400; closing the HTTP client is sufficient and avoids noisy logs.
            transport = streamable_http_client(
                self._server_url, http_client=http, terminate_on_close=False
            )
            async with transport as (read, write, _session_id):
                async with ClientSession(
                    read,
                    write,
                    read_timeout_seconds=timedelta(seconds=self._timeout),
                ) as client:
                    await client.initialize()
                    result = await client.call_tool(_ALLOWED_TOOL, dict(request))

        if result.isError:
            detail = " ".join(
                item.text
                for item in result.content
                if getattr(item, "type", None) == "text"
            )
            raise RuntimeError(f"Robinhood historical request failed: {detail}")
        if result.structuredContent is not None:
            return result.structuredContent
        for item in result.content:
            if getattr(item, "type", None) == "text":
                value = json.loads(item.text)
                if isinstance(value, Mapping):
                    return value
        raise ValueError("Robinhood MCP response did not contain an object")
