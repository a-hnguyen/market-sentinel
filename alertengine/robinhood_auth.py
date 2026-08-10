"""One-time OAuth bootstrap for the Robinhood MCP watcher.

Token exchange is intentionally completed and persisted *before* MCP
initialization. A transport compatibility failure therefore cannot consume a
single-use authorization code without saving the refreshable credential.
"""

import asyncio
import base64
import hashlib
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from . import settings
from .feeds.robinhood_mcp_transport import FileTokenStorage, RobinhoodMCPTransport

_REDIRECT_URI = "http://127.0.0.1:54069/callback"
_METADATA_URL = (
    "https://agent.robinhood.com/.well-known/" "oauth-authorization-server/mcp/trading"
)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def _client_info(
    storage: FileTokenStorage,
    http: httpx.AsyncClient,
    registration_endpoint: str,
) -> OAuthClientInformationFull:
    existing = await storage.get_client_info()
    if existing is not None:
        return existing
    metadata = OAuthClientMetadata(
        client_name="Market Sentinel",
        redirect_uris=[_REDIRECT_URI],
        scope="internal",
        token_endpoint_auth_method="none",
    )
    response = await http.post(
        registration_endpoint,
        json=metadata.model_dump(mode="json", exclude_none=True),
    )
    response.raise_for_status()
    client = OAuthClientInformationFull.model_validate(response.json())
    await storage.set_client_info(client)
    return client


async def authenticate() -> None:
    storage = FileTokenStorage(settings.ROBINHOOD_OAUTH_PATH)
    async with httpx.AsyncClient(timeout=60) as http:
        metadata_response = await http.get(_METADATA_URL)
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        client = await _client_info(
            storage, http, str(metadata["registration_endpoint"])
        )

        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(32)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client.client_id,
                "redirect_uri": _REDIRECT_URI,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": settings.ROBINHOOD_MCP_URL,
                "scope": "internal",
            }
        )
        print("\nOpen this URL in your browser and authorize Robinhood:\n")
        print(f'{metadata["authorization_endpoint"]}?{query}', flush=True)
        raw = await asyncio.to_thread(
            input, "\nPaste the complete localhost callback URL here:\n> "
        )
        callback = parse_qs(urlparse(raw.strip()).query)
        code = callback.get("code", [None])[0]
        returned_state = callback.get("state", [None])[0]
        if not code:
            raise ValueError("callback URL does not contain an authorization code")
        if not returned_state or not secrets.compare_digest(returned_state, state):
            raise ValueError("callback state does not match this authorization attempt")

        token_response = await http.post(
            str(metadata["token_endpoint"]),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _REDIRECT_URI,
                "client_id": client.client_id,
                "code_verifier": verifier,
                "resource": settings.ROBINHOOD_MCP_URL,
            },
        )
        token_response.raise_for_status()
        token = OAuthToken.model_validate(token_response.json())
        await storage.set_tokens(token)

    print(f"\nOAuth state saved to {storage.path}; verifying historical reads...")
    transport = RobinhoodMCPTransport(storage, server_url=settings.ROBINHOOD_MCP_URL)
    await transport.get_equity_historicals(
        {
            "symbols": ["AMC"],
            "start_time": "2026-08-10T04:00:00Z",
            "end_time": "2026-08-10T04:05:00Z",
            "interval": "minute",
            "bounds": "24_5",
            "adjustment_type": "split",
        }
    )
    print("Authentication and read-only historical request verified.")


if __name__ == "__main__":
    try:
        asyncio.run(authenticate())
    except (KeyboardInterrupt, EOFError):
        print("\nAuthentication cancelled.", file=sys.stderr)
        raise SystemExit(1)
