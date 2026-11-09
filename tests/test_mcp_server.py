"""Offline pytest tests for mcp_server.py: a fake Spotify client, no network calls."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mcp_server as ms  # noqa: E402
import playlist_generator as pg  # noqa: E402


def track_obj(n: int, artist: str = "Artist", ms_: int = 200_000, year: str = "1991") -> dict:
    tid = f"{n:022d}"
    return {"id": tid, "uri": f"spotify:track:{tid}", "name": f"Song {n}", "duration_ms": ms_,
            "artists": [{"name": artist}], "album": {"name": "Album", "release_date": year},
            "external_urls": {"spotify": f"https://open.spotify.com/track/{tid}"}}


LIVE = [track_obj(0, "Orbital", 180_000, "1991"), track_obj(1, "Orbital", 600_000, "1993"),
        track_obj(2, "Aphex Twin", 300_000, "1992")]
PLAYLIST = {"id": "P" * 22, "name": "Test Mix", "public": False, "owner": {"id": "me", "display_name": "Me"},
            "items": {"total": 3}, "external_urls": {"spotify": "https://open.spotify.com/playlist/" + "P" * 22}}


class FakeSpotify:
    def __init__(self, live=LIVE):
        self.live = live
        self.calls = []

    def me(self):
        return {"id": "me"}

    def my_playlists(self, include_followed=False):
        return [PLAYLIST, {**PLAYLIST, "id": "Q" * 22, "name": "Other", "owner": {"id": "x"}}][: 2 if include_followed else 1]

    def playlist_tracks(self, playlist_id):
        return self.live

    def call(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if url == "/search":
            return {"tracks": {"items": LIVE[: kw["params"]["limit"]]}}
        return {"description": "Hazy &amp; bright"}


@pytest.fixture
def fake(monkeypatch):
    sp = FakeSpotify()
    monkeypatch.setattr(ms, "spotify", lambda: sp)
    return sp


@pytest.fixture
def tracklist_file(tmp_path):
    def _write(tracks, name="Test Mix"):
        path = tmp_path / "mix.json"
        path.write_text(json.dumps({"name": name, "tracks": tracks}))
        return str(path)
    return _write


def entry(n, section="A"):
    return {"title": f"Song {n}", "artist": "Artist", "section": section,
            "link": f"https://open.spotify.com/track/{n:022d}"}


READ_ONLY_TOOLS = {"list_playlists", "playlist_info", "read_tracklist", "check_playlist",
                   "search_tracks", "list_devices", "now_playing"}
PLAYBACK_TOOLS = {"play_playlist", "play_track", "pause", "resume", "next_track",
                  "previous_track", "set_volume"}


def test_tool_set_and_read_only_tools():
    tools = {t.name: t.annotations for t in asyncio.run(ms.server.list_tools())}
    assert set(tools) == READ_ONLY_TOOLS | PLAYBACK_TOOLS
    for name in READ_ONLY_TOOLS:
        assert tools[name].read_only_hint is True and tools[name].destructive_hint is False, name
    assert not any(tools[n].destructive_hint for n in tools)   # nothing can change playlists


def test_list_playlists_filters(fake):
    assert [p["name"] for p in ms.list_playlists()] == ["Test Mix"]
    assert [p["name"] for p in ms.list_playlists(include_followed=True)] == ["Test Mix", "Other"]
    assert ms.list_playlists(name_filter="MIX")[0]["tracks"] == 3
    assert ms.list_playlists(name_filter="nothing") == []


def test_playlist_info_stats_and_tracklist(fake):
    info = ms.playlist_info("Test Mix")
    assert info["tracks"] == 3 and info["artists"] == 2
    assert info["total_length"] == "18:00" and info["average_length"] == "6:00"
    assert info["shortest"]["title"] == "Song 0" and info["longest"]["title"] == "Song 1"
    assert info["top_artists"][0] == {"artist": "Orbital", "tracks": 2}
    assert info["album_years"] == {"first": 1991, "last": 1993, "by_decade": {"1990s": 3}}
    assert info["description"] == "Hazy & bright"                    # HTML entities decoded
    assert [t["position"] for t in info["tracklist"]] == [1, 2, 3]
    assert info["tracklist"][0]["link"].endswith("0" * 22)


def test_playlist_info_unknown_playlist_is_a_tool_error(fake):
    with pytest.raises(ToolError, match="No playlist named"):
        ms.playlist_info("Nope")


def test_check_playlist_match_and_mismatch(fake, tracklist_file):
    ok = ms.check_playlist(tracklist_file([entry(0), entry(1), entry(2, "B")]))
    assert ok["matches"] is True and ok["mismatches"] == []
    assert ok["sections"] == [{"section": "A", "tracks": 2, "length": "13:00"},
                              {"section": "B", "tracks": 1, "length": "5:00"}]
    bad = ms.check_playlist(tracklist_file([entry(1), entry(0), entry(2)]))
    assert bad["matches"] is False
    assert [m["position"] for m in bad["mismatches"]] == [1, 2]


def test_check_playlist_needs_one_named_playlist(fake, tracklist_file):
    with pytest.raises(ToolError, match="found 0"):
        ms.check_playlist(tracklist_file([entry(0)], name="Missing"))


def test_read_tracklist_resolves_relative_to_repo(tmp_path, monkeypatch, tracklist_file):
    path = Path(tracklist_file([entry(0), {**entry(1), "skip": True}]))
    monkeypatch.setattr(ms, "REPO", path.parent)
    data = ms.read_tracklist(path.name)
    assert data["name"] == "Test Mix" and len(data["tracks"]) == 2


@pytest.mark.parametrize("bad, message", [("notes.txt", "must be a .yaml"), ("missing.yaml", "No such tracklist")])
def test_read_tracklist_rejects_bad_paths(bad, message):
    with pytest.raises(ToolError, match=message):
        ms.read_tracklist(bad)


def test_search_tracks_clamps_limit(fake):
    assert len(ms.search_tracks("orbital", limit=2)) == 2
    ms.search_tracks("orbital", limit=50)
    assert fake.calls[-1][2]["params"]["limit"] == 10


def test_spotify_errors_become_tool_errors(monkeypatch):
    def boom():
        raise pg.SpotifyError("GET /me → 401")
    monkeypatch.setattr(ms, "spotify", boom)
    with pytest.raises(ToolError, match="Spotify error: GET /me"):
        ms.list_playlists()


def test_cli_output_goes_to_stderr_not_stdout(monkeypatch, capsys):
    def noisy():
        print("Opening your browser to approve access...")     # what sign-in prints
        return FakeSpotify()
    monkeypatch.setattr(ms, "spotify", noisy)
    ms.list_playlists()
    out = capsys.readouterr()
    assert out.out == "" and "Opening your browser" in out.err  # stdout is reserved for MCP


# ---------------------------------------------------------------- playback tools

class FakePlayer(FakeSpotify):
    """Fake Spotify with devices and a player; records every call."""

    def __init__(self, devices=None, resume_at_ms=0):
        super().__init__()
        self.devices = [{"id": "WEB", "name": "Web Player (Chrome)", "type": "Computer",
                         "is_active": False, "volume_percent": 100}] if devices is None else devices
        self.state = {}
        self.resume_at_ms = resume_at_ms   # Spotify sometimes resumes an old position

    def call(self, method, url, **kw):
        self.calls.append((method, url, kw))
        params, body = kw.get("params") or {}, kw.get("json")
        if url == "/me/player/devices":
            return {"devices": self.devices}
        if url == "/me/player" and method == "GET":
            return self.state
        if url == "/me/player" and method == "PUT":
            for d in self.devices:
                d["is_active"] = d["id"] in body["device_ids"]
        elif url == "/me/player/volume":
            self.devices[0]["volume_percent"] = params["volume_percent"]
        elif url == "/me/player/play":
            if body:
                pos = body.get("offset", {}).get("position", 0)
                self.state = {"is_playing": True, "item": LIVE[pos] if "context_uri" in body else LIVE[0],
                              "progress_ms": self.resume_at_ms, "device": self.devices[0],
                              "context": {"uri": body.get("context_uri")} if "context_uri" in body else None}
            else:
                self.state["is_playing"] = True
        elif url == "/me/player/seek":
            self.state["progress_ms"] = params["position_ms"]
        elif url == "/me/player/pause":
            self.state["is_playing"] = False
        elif url == "/search":
            return {"tracks": {"items": LIVE[:1]}}
        return {}

    def paths(self, method=None):
        return [u for m, u, _ in self.calls if method in (None, m)]


@pytest.fixture
def player(monkeypatch):
    sp = FakePlayer()
    monkeypatch.setattr(ms, "spotify", lambda: sp)
    monkeypatch.setattr(ms.time, "sleep", lambda s: None)
    return sp


def test_playback_tools_are_marked_as_changing_playback_only():
    tools = {t.name: t.annotations for t in asyncio.run(ms.server.list_tools())}
    for name in ("play_playlist", "play_track", "pause", "resume", "next_track", "previous_track", "set_volume"):
        assert tools[name].read_only_hint is False and tools[name].destructive_hint is False, name
    for name in ("list_devices", "now_playing"):
        assert tools[name].read_only_hint is True, name


def test_server_asks_for_playback_permissions():
    assert set(ms.PLAYBACK_SCOPES) <= set(pg.SCOPES.split())


def test_play_playlist_activates_device_sets_volume_and_starts_at_track(player):
    state = ms.play_playlist("Test Mix", track_number=2, volume=30)
    assert player.paths("PUT")[:3] == ["/me/player", "/me/player/volume", "/me/player/play"]
    play = next(kw for m, u, kw in player.calls if u == "/me/player/play")
    assert play["json"] == {"context_uri": f"spotify:playlist:{PLAYLIST['id']}",
                            "offset": {"position": 1}, "position_ms": 0}
    assert play["params"] == {"device_id": "WEB"}
    assert state["playing"] is True and state["track"]["title"] == "Song 1"
    assert state["device"]["volume"] == 30 and "/me/player/seek" not in player.paths()


def test_play_playlist_rewinds_when_spotify_resumes_mid_track(monkeypatch):
    sp = FakePlayer(resume_at_ms=116_000)
    monkeypatch.setattr(ms, "spotify", lambda: sp)
    monkeypatch.setattr(ms.time, "sleep", lambda s: None)
    state = ms.play_playlist("Test Mix")
    assert "/me/player/seek" in sp.paths() and state["position"] == "0:00"


def test_play_playlist_rejects_bad_track_number(player):
    with pytest.raises(ToolError, match="between 1 and 3"):
        ms.play_playlist("Test Mix", track_number=4)


def test_play_track_by_link_and_by_search(player):
    ms.play_track("https://open.spotify.com/track/" + "0" * 22)
    play = [kw for m, u, kw in player.calls if u == "/me/player/play"][-1]
    assert play["json"] == {"uris": ["spotify:track:" + "0" * 22], "position_ms": 0}
    ms.play_track("Rez Underworld")
    assert "/search" in player.paths()


@pytest.mark.parametrize("devices, device, message", [
    ([], "", "No Spotify device is available"),
    ([{"id": "A", "name": "Mac"}, {"id": "B", "name": "Phone"}], "", "No device is active. Say which one"),
    ([{"id": "A", "name": "Mac"}], "kitchen", "No device matches 'kitchen'"),
])
def test_device_choice_errors(monkeypatch, devices, device, message):
    monkeypatch.setattr(ms, "spotify", lambda: FakePlayer(devices=devices))
    with pytest.raises(ToolError, match=message):
        ms.pause(device)


def test_device_chosen_by_name(monkeypatch):
    sp = FakePlayer(devices=[{"id": "A", "name": "Mac", "is_active": True}, {"id": "B", "name": "Kitchen speaker"}])
    monkeypatch.setattr(ms, "spotify", lambda: sp)
    monkeypatch.setattr(ms.time, "sleep", lambda s: None)
    ms.next_track("kitchen")
    assert sp.calls[-2][1:] == ("/me/player/next", {"params": {"device_id": "B"}})


def test_pause_resume_skip_and_volume(player):
    ms.play_playlist("Test Mix")
    assert ms.pause()["playing"] is False
    assert ms.resume()["playing"] is True
    ms.next_track(); ms.previous_track()
    assert ms.set_volume(150)["device"]["volume"] == 100       # clamped to 0-100
    assert {"/me/player/next", "/me/player/previous"} <= set(player.paths("POST"))


def test_now_playing_when_idle(player):
    assert ms.now_playing() == {"playing": False, "note": "Nothing is playing on any device."}


def test_list_devices(player):
    assert ms.list_devices() == [{"name": "Web Player (Chrome)", "id": "WEB", "type": "Computer",
                                  "active": False, "volume": 100}]


def test_non_json_player_reply_is_tolerated(monkeypatch):
    class Reply:
        status_code, ok, content, headers, text = 200, True, b"i4A8Y-28RTcH75FV40NRbdykQM4", {}, "i4A8Y"
        def json(self):
            raise ValueError("not JSON")
    sp = ms.ServerSpotify("token")
    monkeypatch.setattr(sp.s, "request", lambda *a, **kw: Reply())
    assert sp.call("PUT", "/me/player/pause") == {}
