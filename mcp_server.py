#!/usr/bin/env python3
"""
mcp_server.py — a local MCP server that lets Claude read and play your Spotify playlists.

It never changes your playlists or library. The read-only tools read from Spotify
or local tracklist files; the playback tools only control what's playing on a
Spotify app you have open. It reuses playlist_generator.py's Spotify client,
sign-in and .env settings.

Read-only tools
  list_playlists   Your playlists with track counts, visibility, IDs and links.
  playlist_info    One playlist's details, stats and full tracklist.
  read_tracklist   A local YAML/JSON tracklist (sections, notes, links, skips).
  check_playlist   Whether a Spotify playlist matches a local tracklist file.
  search_tracks    Search Spotify for tracks, to find links for a tracklist.
  list_devices     Spotify apps that can play (Mac, phone, web player, speakers).
  now_playing      What's playing, where, and how far in.

Playback tools (need Premium and an open Spotify app)
  play_playlist    Play a playlist, optionally from a given track number.
  play_track       Play one track, by link or by search.
  pause, resume, next_track, previous_track, set_volume

Run (normally started by Claude Code or Claude Desktop, not by hand)
  ~/.virtualenvs/spotify/bin/python mcp_server.py

Sign in once with the CLI first (python playlist_generator.py list), so the cached
token exists; the server refreshes it as needed.
"""

import contextlib
import html
import sys
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

import playlist_generator as pg

REPO = Path(__file__).resolve().parent

# The playback tools need two more Spotify permissions. Only this server asks for them:
# playlist_generator.py's own SCOPES are unchanged, and a token with extra permissions
# still satisfies the CLI, so the CLI is unaffected. The first tool call after adding
# them opens the browser once to approve.
PLAYBACK_SCOPES = ("user-read-playback-state", "user-modify-playback-state")
pg.SCOPES = " ".join(dict.fromkeys(pg.SCOPES.split() + list(PLAYBACK_SCOPES)))
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=True)
# Playback changes what's playing, not your playlists or library.
PLAYBACK = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                           idempotent_hint=False, open_world_hint=True)

server = MCPServer(
    name="spotify-playlists",
    version=pg.__version__,
    instructions=(
        "Access to the user's Spotify playlists, local tracklist files and playback. "
        "Use playlist_info for a playlist's stats and tracklist, check_playlist to compare a "
        "playlist with its YAML/JSON tracklist, and search_tracks to find Spotify links. "
        "The playback tools (play_playlist, play_track, pause, resume, next_track, "
        "previous_track, set_volume) control a Spotify app the user has open; use "
        "list_devices first if no device is active. No tool can change playlists or the "
        "library; for that, give the user the matching playlist_generator.py command "
        "(create --replace, info --set-description, delete)."
    ),
)


@contextlib.contextmanager
def spotify_errors():
    """Turn the CLI's exits and Spotify errors into tool errors.

    stdout carries the MCP protocol over stdio, so anything the shared CLI code
    prints (sign-in messages, progress) is sent to stderr instead.
    """
    with contextlib.redirect_stdout(sys.stderr):
        try:
            yield
        except SystemExit as err:
            raise ToolError(str(err.code)) from None
        except pg.SpotifyError as err:
            raise ToolError(f"Spotify error: {err}") from None


class ServerSpotify(pg.Spotify):
    """The CLI's Spotify client, tolerating player endpoints that reply with a bare text ID."""

    def call(self, method, url, **kw):
        try:
            return super().call(method, url, **kw)
        except ValueError:   # 2xx reply that isn't JSON (e.g. PUT /me/player/pause)
            return {}


def spotify() -> pg.Spotify:
    return ServerSpotify(pg.get_access_token(pg.client_id()))


def tracklist_path(tracklist_file: str) -> str:
    """Resolve a tracklist path (relative paths are relative to this repo); YAML/JSON only."""
    path = Path(tracklist_file).expanduser()
    if not path.is_absolute():
        path = REPO / path
    if path.suffix.lower() not in (".yaml", ".yml", ".json"):
        raise ToolError("tracklist_file must be a .yaml, .yml or .json file")
    if not path.is_file():
        raise ToolError(f"No such tracklist file: {path}")
    return str(path)


def track_summary(t: dict, position: int | None = None) -> dict:
    out = {} if position is None else {"position": position}
    return out | {
        "artist": pg.track_artists(t),
        "title": t.get("name"),
        "length": pg.fmt_ms(t.get("duration_ms", 0)),
        "album": t.get("album", {}).get("name"),
        "album_year": pg.album_year(t) or None,
        "link": t.get("external_urls", {}).get("spotify") or f"https://open.spotify.com/track/{t.get('id')}",
    }


def playlist_summary(p: dict) -> dict:
    return {"name": p.get("name"), "id": p["id"], "tracks": pg.count_items(p),
            "public": p.get("public"), "owner": p.get("owner", {}).get("display_name"),
            "url": pg.playlist_url(p)}


def find_one_playlist(sp: pg.Spotify, playlist: str) -> dict:
    matches = pg.find_playlists(sp, playlist)
    if len(matches) > 1:
        ids = ", ".join(p["id"] for p in matches)
        raise ToolError(f"{len(matches)} playlists are called '{playlist}' ({ids}); pass one ID.")
    return matches[0]


@server.tool(annotations=READ_ONLY)
def list_playlists(name_filter: str = "", include_followed: bool = False) -> list[dict]:
    """List the user's Spotify playlists (owned ones, plus followed ones if include_followed).

    name_filter keeps only playlists whose name contains this text (case-insensitive).
    """
    with spotify_errors():
        playlists = spotify().my_playlists(include_followed=include_followed)
    return [playlist_summary(p) for p in playlists
            if name_filter.lower() in (p.get("name") or "").lower()]


@server.tool(annotations=READ_ONLY)
def playlist_info(playlist: str) -> dict:
    """Details, stats and the full tracklist of one playlist.

    playlist is a name, a 22-character ID, a spotify:playlist: URI or an open.spotify.com link.
    Album years come from the album on Spotify, so remasters and compilations can show a
    later year than the original release.
    """
    with spotify_errors():
        sp = spotify()
        pl = find_one_playlist(sp, playlist)
        try:
            pl = {**pl, **sp.call("GET", f"/playlists/{pl['id']}",
                                  params={"fields": "name,description,public,owner,external_urls,id"})}
        except pg.SpotifyError:
            pass
        tracks = sp.playlist_tracks(pl["id"])
    info = {**playlist_summary(pl), "description": html.unescape(pl.get("description") or ""),
            "tracks": len(tracks)}
    if not tracks:
        info["note"] = "No readable tracks: Spotify only lets apps read playlists you own."
        return info
    st = pg.playlist_stats(tracks)
    info.update({
        "artists": len(st["artists"]),
        "total_length": pg.fmt_ms(st["total_ms"]),
        "average_length": pg.fmt_ms(st["average_ms"]),
        "shortest": track_summary(st["shortest"]),
        "longest": track_summary(st["longest"]),
        "top_artists": [{"artist": a, "tracks": n} for a, n in st["artists"].most_common(5)],
        "album_years": {"first": st["years"][0], "last": st["years"][-1],
                        "by_decade": dict(sorted(st["decades"].items()))} if st["years"] else None,
        "tracklist": [track_summary(t, i) for i, t in enumerate(tracks, 1)],
    })
    return info


@server.tool(annotations=READ_ONLY)
def read_tracklist(tracklist_file: str) -> dict:
    """Read a local YAML or JSON tracklist file, including sections, notes and skipped entries.

    tracklist_file is a path; relative paths are relative to the playlist_generator repo
    (for example party_2026.yaml).
    """
    path = tracklist_path(tracklist_file)
    with spotify_errors():
        data = pg.load_tracklist(path)
    return {"file": path, **data}


@server.tool(annotations=READ_ONLY)
def check_playlist(tracklist_file: str, playlist_name: str = "") -> dict:
    """Check whether a Spotify playlist matches a local tracklist file, track by track.

    Uses the file's name unless playlist_name is given. Returns matches (true/false), section
    totals, and every position that differs, with what the file expects and what Spotify has.
    """
    path = tracklist_path(tracklist_file)
    with spotify_errors():
        data = pg.load_tracklist(path)
        name = playlist_name or data.get("name")
        if not name:
            raise ToolError("The file has no 'name'; pass playlist_name.")
        sp = spotify()
        found = [p for p in sp.my_playlists() if p.get("name") == name]
        if len(found) != 1:
            raise ToolError(f"Expected one playlist you own called '{name}', found {len(found)}.")
        pairs, problems = pg.resolve(sp, [e for e in data["tracks"] if not e.get("skip")], quiet=True)
        live = sp.playlist_tracks(found[0]["id"])
    rows = pg.compare_tracks(pairs, live)
    sections = {}
    for r in rows:
        sec = sections.setdefault(r["section"], {"section": r["section"], "tracks": 0, "ms": 0})
        sec["tracks"] += 1
        sec["ms"] += r["duration_ms"]
    mismatches = [{k: r[k] for k in ("position", "section", "file", "spotify")}
                  for r in rows if not r["ok"]]
    return {
        "matches": not mismatches and not problems,
        "playlist": playlist_summary(found[0]),
        "file": path,
        "tracks_on_spotify": len(live),
        "total_length": pg.fmt_ms(sum(r["duration_ms"] for r in rows)),
        "sections": [{"section": s["section"], "tracks": s["tracks"], "length": pg.fmt_ms(s["ms"])}
                     for s in sections.values()],
        "mismatches": mismatches,
        "unresolved": problems,
    }


@server.tool(annotations=READ_ONLY)
def search_tracks(query: str, limit: int = 5) -> list[dict]:
    """Search Spotify for tracks.

    query is free text, or Spotify search syntax such as: track:"Rez" artist:"Underworld".
    limit is 1-10 (Spotify's maximum for apps in Development Mode). Each result includes
    the open.spotify.com link to paste into a tracklist file.
    """
    limit = max(1, min(limit, 10))
    with spotify_errors():
        res = spotify().call("GET", "/search", params={"q": query, "type": "track", "limit": limit})
    return [track_summary(t) for t in res.get("tracks", {}).get("items", []) if t]


# ---------------------------------------------------------------- playback

NO_DEVICE = ("No Spotify device is available. Open the Spotify app (Mac, phone or speaker), "
             "or in the web player press play once so it registers, then try again.")


def device_summary(d: dict) -> dict:
    return {"name": d.get("name"), "id": d.get("id"), "type": d.get("type"),
            "active": d.get("is_active"), "volume": d.get("volume_percent")}


def pick_device(sp: pg.Spotify, device: str = "") -> dict:
    """The device named (by ID or part of its name), else the active one, else the only one."""
    devices = sp.call("GET", "/me/player/devices").get("devices", [])
    if not devices:
        raise ToolError(NO_DEVICE)
    if device:
        hits = [d for d in devices if d.get("id") == device or device.lower() in (d.get("name") or "").lower()]
        if len(hits) == 1:
            return hits[0]
        names = ", ".join(d.get("name") or "?" for d in devices)
        raise ToolError(f"{'No' if not hits else 'More than one'} device matches '{device}'. Devices: {names}.")
    active = [d for d in devices if d.get("is_active")]
    if active:
        return active[0]
    if len(devices) == 1:
        return devices[0]
    names = ", ".join(d.get("name") or "?" for d in devices)
    raise ToolError(f"No device is active. Say which one to use: {names}.")


def playback_state(sp: pg.Spotify) -> dict:
    """What's playing now, as plain data ({"playing": False} when nothing is)."""
    st = sp.call("GET", "/me/player")
    item = st.get("item") if st else None
    if not item:
        return {"playing": False, "note": "Nothing is playing on any device."}
    ctx = st.get("context") or {}
    return {
        "playing": bool(st.get("is_playing")),
        "track": track_summary(item),
        "position": pg.fmt_ms(st.get("progress_ms") or 0),
        "device": device_summary(st.get("device") or {}),
        "context": ctx.get("uri"),
        "shuffle": st.get("shuffle_state"),
        "repeat": st.get("repeat_state"),
    }


def start_on(sp: pg.Spotify, dev: dict, body: dict | None, volume: int | None):
    """Make dev the active device, set volume if asked, start playback, then confirm."""
    did = dev["id"]
    if not dev.get("is_active"):
        sp.call("PUT", "/me/player", json={"device_ids": [did], "play": False})
        time.sleep(1)                  # Spotify needs a moment before the device accepts commands
    if volume is not None:
        sp.call("PUT", "/me/player/volume", params={"volume_percent": max(0, min(volume, 100)),
                                                    "device_id": did})
    sp.call("PUT", "/me/player/play", params={"device_id": did}, json=body)
    time.sleep(1.5)
    state = playback_state(sp)
    # Spotify sometimes resumes an old position in the track instead of starting it from 0.
    if body and state.get("track") and (sp.call("GET", "/me/player").get("progress_ms") or 0) > 5000:
        sp.call("PUT", "/me/player/seek", params={"position_ms": 0, "device_id": did})
        time.sleep(1)
        state = playback_state(sp)
    return state


@server.tool(annotations=READ_ONLY)
def list_devices() -> list[dict]:
    """Spotify devices that can play right now (Mac or phone app, web player, speakers).

    A web player only appears after play has been pressed in it once.
    """
    with spotify_errors():
        return [device_summary(d) for d in spotify().call("GET", "/me/player/devices").get("devices", [])]


@server.tool(annotations=READ_ONLY)
def now_playing() -> dict:
    """What's playing: track, position, device, volume and the playlist it's playing from."""
    with spotify_errors():
        return playback_state(spotify())


@server.tool(annotations=PLAYBACK)
def play_playlist(playlist: str, track_number: int = 1, device: str = "", volume: int | None = None) -> dict:
    """Play a playlist from the start, or from track_number (1 = first track).

    playlist is a name, ID, URI or link. device is a device name or ID (default: the active
    device, or the only one). volume is 0-100, set before playback starts. Returns what's
    playing afterwards.
    """
    with spotify_errors():
        sp = spotify()
        pl = find_one_playlist(sp, playlist)
        total = pg.count_items(pl)
        if track_number < 1 or (total and track_number > total):
            raise ToolError(f"track_number must be between 1 and {total or 'the playlist length'}.")
        dev = pick_device(sp, device)
        return start_on(sp, dev, {"context_uri": f"spotify:playlist:{pl['id']}",
                                  "offset": {"position": track_number - 1}, "position_ms": 0}, volume)


@server.tool(annotations=PLAYBACK)
def play_track(track: str, device: str = "", volume: int | None = None) -> dict:
    """Play one track. track is a Spotify track link, URI or ID, or a search such as "Rez Underworld"
    (the top search result is played). device and volume work as in play_playlist.
    """
    with spotify_errors():
        sp = spotify()
        kind, tid = pg.parse_link(track)
        if kind != "track":
            if len(track) == 22 and track.isalnum():
                tid = track
            else:
                hits = sp.call("GET", "/search", params={"q": track, "type": "track", "limit": 1})
                items = [t for t in hits.get("tracks", {}).get("items", []) if t]
                if not items:
                    raise ToolError(f"No track found for '{track}'.")
                tid = items[0]["id"]
        dev = pick_device(sp, device)
        return start_on(sp, dev, {"uris": [f"spotify:track:{tid}"], "position_ms": 0}, volume)


def control(method: str, path: str, device: str, params: dict | None = None) -> dict:
    with spotify_errors():
        sp = spotify()
        dev = pick_device(sp, device)
        sp.call(method, path, params={**(params or {}), "device_id": dev["id"]})
        time.sleep(1)
        return playback_state(sp)


@server.tool(annotations=PLAYBACK)
def pause(device: str = "") -> dict:
    """Pause playback (on the active device, or the named one)."""
    return control("PUT", "/me/player/pause", device)


@server.tool(annotations=PLAYBACK)
def resume(device: str = "") -> dict:
    """Resume playback where it was paused."""
    return control("PUT", "/me/player/play", device)


@server.tool(annotations=PLAYBACK)
def next_track(device: str = "") -> dict:
    """Skip to the next track."""
    return control("POST", "/me/player/next", device)


@server.tool(annotations=PLAYBACK)
def previous_track(device: str = "") -> dict:
    """Go back to the previous track (or the start of this one)."""
    return control("POST", "/me/player/previous", device)


@server.tool(annotations=PLAYBACK)
def set_volume(percent: int, device: str = "") -> dict:
    """Set the volume, 0-100."""
    return control("PUT", "/me/player/volume", device, {"volume_percent": max(0, min(percent, 100))})


def main():
    pg.load_dotenv()
    server.run("stdio")


if __name__ == "__main__":
    main()
