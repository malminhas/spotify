"""Start mcp_server.py over stdio with a fake Spotify, for tests/test_mcp_stdio.py.

Not a test itself: the stdio test runs this file as the server process, so the real
MCP protocol is exercised without signing in to or calling Spotify.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mcp_server as ms  # noqa: E402

PLAYLIST = {"id": "P" * 22, "name": "Test Mix", "public": False,
            "owner": {"id": "me", "display_name": "Me"}, "items": {"total": 2}}
TRACKS = [{"id": f"{i:022d}", "uri": f"spotify:track:{i:022d}", "name": f"Song {i}",
           "duration_ms": 200_000, "artists": [{"name": "Orbital"}],
           "album": {"name": "Album", "release_date": "1991"}} for i in range(2)]


class FakeSpotify:
    def __init__(self):
        print("Opening your browser to approve access...")   # sign-in noise must not reach stdout

    def me(self):
        return {"id": "me"}

    def my_playlists(self, include_followed=False):
        return [PLAYLIST]

    def playlist_tracks(self, playlist_id):
        return TRACKS

    def call(self, method, url, **kw):
        if url == "/me/player/devices":
            return {"devices": [{"id": "WEB", "name": "Web Player (Chrome)", "type": "Computer",
                                 "is_active": True, "volume_percent": 30}]}
        return {}


ms.spotify = FakeSpotify
ms.time.sleep = lambda s: None

if __name__ == "__main__":
    ms.server.run("stdio")
