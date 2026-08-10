import asyncio
import json

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from alertengine.feeds.robinhood_mcp_transport import FileTokenStorage


def test_file_token_storage_round_trips_oauth_state_atomically(tmp_path):
    path = tmp_path / "oauth.json"
    storage = FileTokenStorage(path)
    token = OAuthToken(
        access_token="access",
        refresh_token="refresh",
        expires_in=3600,
        scope="internal",
    )
    client = OAuthClientInformationFull(
        client_id="client-id",
        redirect_uris=["http://127.0.0.1:54069/callback"],
    )

    async def drive():
        await storage.set_tokens(token)
        await storage.set_client_info(client)
        return await storage.get_tokens(), await storage.get_client_info()

    saved_token, saved_client = asyncio.run(drive())

    assert saved_token == token
    assert saved_client == client
    assert path.stat().st_mode & 0o777 == 0o600
    assert not path.with_suffix(".json.tmp").exists()
    payload = json.loads(path.read_text())
    assert payload["tokens"]["refresh_token"] == "refresh"
    assert payload["client_info"]["client_id"] == "client-id"


def test_file_token_storage_returns_none_before_bootstrap(tmp_path):
    storage = FileTokenStorage(tmp_path / "missing.json")

    async def drive():
        return await storage.get_tokens(), await storage.get_client_info()

    assert asyncio.run(drive()) == (None, None)
