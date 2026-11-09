"""End-to-end test of mcp_server.py over the real MCP stdio protocol, as Claude connects to it.

The server runs as a separate process (tests/fake_spotify_server.py) with a fake Spotify,
so nothing signs in to or calls Spotify.
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

LAUNCHER = Path(__file__).resolve().parent / "fake_spotify_server.py"
sys.path.insert(0, str(LAUNCHER.parent.parent))
import playlist_generator as pg  # noqa: E402


def result(res):
    """A tool result's data, whether it came back structured or as JSON text."""
    if res.structured_content is not None:
        return res.structured_content.get("result", res.structured_content)
    return json.loads(res.content[0].text)


async def session_run():
    params = StdioServerParameters(command=sys.executable, args=[str(LAUNCHER)], cwd="/")
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        tools = (await session.list_tools()).tools
        calls = {
            "list_playlists": await session.call_tool("list_playlists", {}),
            "playlist_info": await session.call_tool("playlist_info", {"playlist": "Test Mix"}),
            "list_devices": await session.call_tool("list_devices", {}),
            "unknown": await session.call_tool("playlist_info", {"playlist": "Nope"}),
            "bad_file": await session.call_tool("read_tracklist", {"tracklist_file": "/etc/passwd"}),
        }
        return init, tools, calls


def test_server_over_stdio():
    init, tools, calls = asyncio.run(asyncio.wait_for(session_run(), timeout=60))

    assert init.server_info.name == "spotify-playlists"
    assert init.server_info.version == pg.__version__
    assert len(tools) == 14
    assert {t.name for t in tools if t.annotations.read_only_hint} == {
        "list_playlists", "playlist_info", "read_tracklist", "check_playlist",
        "search_tracks", "list_devices", "now_playing"}

    assert not calls["list_playlists"].is_error
    assert [p["name"] for p in result(calls["list_playlists"])] == ["Test Mix"]

    info = result(calls["playlist_info"])
    assert info["tracks"] == 2 and info["total_length"] == "6:40"
    assert [t["title"] for t in info["tracklist"]] == ["Song 0", "Song 1"]

    assert result(calls["list_devices"])[0]["name"] == "Web Player (Chrome)"

    # Errors come back as tool errors; the server keeps running for the calls after them.
    assert calls["unknown"].is_error and "No playlist named 'Nope'" in calls["unknown"].content[0].text
    assert calls["bad_file"].is_error and "must be a .yaml" in calls["bad_file"].content[0].text
