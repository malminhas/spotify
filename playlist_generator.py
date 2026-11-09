#!/usr/bin/env python3
"""
playlist_generator.py — build Spotify playlists from YAML or JSON tracklists.

Version 1.2.1, 2026-11-09, by Mal Minhas (python playlist_generator.py -V)

Commands
  create FILE      Create a playlist from a YAML/JSON tracklist, in order.
  list             List playlists you own (use --all to include followed ones).
  delete TARGET    Remove a playlist (by name, ID, URI or link) from your library.
  check FILE       Check the Spotify playlist matches the file; prints it by section.
  info TARGET      Summary (length, tracks, artists, years), an AI-written overview
                   (Claude or OpenAI, chosen with --model) and the full tracklist.
                   --set-description writes a short AI description to Spotify.

Examples
  python playlist_generator.py create party_2026.yaml
  python playlist_generator.py create party_2026.yaml --dry-run
  python playlist_generator.py create party_2026.yaml --name "Party 2026 v2" --public
  python playlist_generator.py list
  python playlist_generator.py check party_2026.yaml
  python playlist_generator.py info "Party 2026"
  python playlist_generator.py delete "Party 2026"
  python playlist_generator.py delete https://open.spotify.com/playlist/1ks0R4jGZ8iGGrpJSzilch --yes

One-off setup
  1. https://developer.spotify.com/dashboard → Create app → choose "Web API".
     Redirect URI: http://127.0.0.1:8888/callback  (must match exactly)
  2. pip install -r requirements.txt   (requests; pyyaml for YAML input;
                                        anthropic / openai for info's AI summary)
  3. Put SPOTIFY_CLIENT_ID=<your app's client id> in a .env file next to this
     script (git-ignored), or export it in your shell. For info's AI features,
     also add ANTHROPIC_API_KEY and/or OPENAI_API_KEY.

The first run opens a browser to approve access. The refresh token is cached
in ~/.config/spotify-playlist/token.json, so later runs don't need the browser.

Tracklist format (YAML shown; JSON with the same keys works too)

  name: Party 2026                 # playlist name (required unless --name)
  description: Psych to ambient    # optional
  public: false                    # optional, default false
  tracks:
    - title: Cherry-Coloured Funk
      artist: Cocteau Twins
      style: Dream pop              # free text, for your own reference
      section: Arrival              # groups tracks in check's output
      link: https://open.spotify.com/track/37pKTyMwalomKCZjxTc2QZ
      length: "3:12"                # free text, for your own reference
      notes: anything you like
    - title: Some song with no link yet
      artist: Some Artist           # no link → looked up with Spotify search
    - title: My mix
      link: https://open.spotify.com/playlist/4afTuHYJ0R3j0zU4Kpd7tb
                                    # playlist link → all its tracks, in order
                                    # (only works for playlists you own)
    - title: Substrata
      link: https://open.spotify.com/album/5QIf4hNIAksV1uMCXHVkAZ
      discs: [1]                    # album link → its tracks; optional disc filter
      track_numbers: [1, 2, 3]      # optional: only these track numbers
    - title: Skipped for now
      artist: Someone
      skip: true                    # left out of the playlist

  Links may be open.spotify.com URLs or spotify:track:/album:/playlist: URIs.
  Entries are added in file order.

Notes
  Written for the Spotify Web API as it stands after the February 2026
  Development Mode changes: POST /me/playlists, POST /playlists/{id}/items,
  DELETE /me/library, search limit of 10. The app owner needs Premium.

Release notes
  1.2.1 (2026-11-09)  More tests.
    - CLI: list, delete, create --dry-run, check and info output, and .env loading.
    - MCP: an end-to-end test over the real stdio protocol (test_mcp_stdio.py,
      with fake_spotify_server.py as the server). 76 tests in all.
    - No change to how the CLI or the MCP server behaves.

  1.2.0 (2026-11-08)  Playback in the MCP server.
    - mcp_server.py adds list_devices and now_playing (read-only) and
      play_playlist, play_track, pause, resume, next_track, previous_track and
      set_volume. They control playback only, never playlists or the library.
    - Only the MCP server asks for the two playback permissions; the CLI's
      permissions and behaviour are unchanged.
    - 14 more tests, 61 in all.

  1.1.0 (2026-11-08)  Read-only MCP server.
    - mcp_server.py: a local MCP server so Claude Code and Claude Desktop can
      read playlists: list_playlists, playlist_info, read_tracklist,
      check_playlist and search_tracks. It can't change Spotify.
    - playlist_stats() and compare_tracks() now hold the info and check logic,
      shared by the CLI and the MCP server; CLI output is unchanged.
    - 12 more tests (tests/test_mcp_server.py), 47 in all.

  1.0.1 (2026-11-08)  Tests.
    - tests/test_playlist_generator.py: 35 offline pytest tests (fake Spotify
      client and fake AI replies, no network calls) covering tracklists,
      create --replace, AI model choice, description rules, --set-description
      and -V. Run with: pytest
    - No change to how the tool behaves.

  1.0.0 (2026-11-08)  First release.
    - create: build a playlist from a YAML/JSON tracklist in file order, from
      track, album (disc/track filters) and playlist links or artist + title
      search; --dry-run previews, --replace updates in place and keeps the link.
    - check: verify the Spotify playlist matches the file, track by track,
      printed by section with lengths; exits 1 on a mismatch.
    - info: stats, full tracklist and an AI-written overview (Claude Opus 5.5
      by default, or OpenAI with --model openai); --set-description writes a
      short AI description to Spotify within its 300-character limit.
    - list / delete: manage your playlists by name, ID or link.
    - PKCE sign-in with a cached token; settings and API keys read from .env.
"""

__version__ = "1.2.1"
__date__ = "2026-11-09"
__author__ = "Mal Minhas"

import argparse
import collections
import base64
import hashlib
import html
import http.server
import itertools
import json
import os
import re
import secrets
import sys
import textwrap
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("This script needs the 'requests' package: pip install requests")

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = " ".join([
    "playlist-read-private",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-library-modify",
])
API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
TOKEN_FILE = Path.home() / ".config" / "spotify-playlist" / "token.json"

LINK_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]+/)?|spotify:)"
    r"(track|album|playlist)[/:]([A-Za-z0-9]{22})"
)


# ---------------------------------------------------------------- input file

def load_tracklist(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        sys.exit(f"File not found: {path}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            sys.exit("YAML input needs PyYAML: pip install pyyaml (or use a .json file)")
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    if isinstance(data, list):          # bare list of tracks is allowed
        data = {"tracks": data}
    if not isinstance(data, dict) or not isinstance(data.get("tracks"), list):
        sys.exit(f"{path}: expected a 'tracks' list")
    for i, entry in enumerate(data["tracks"], 1):
        if not isinstance(entry, dict):
            sys.exit(f"{path}: track {i} is not a mapping")
        if not entry.get("link") and not (entry.get("title") and entry.get("artist")):
            sys.exit(f"{path}: track {i} needs a 'link', or both 'title' and 'artist'")
        if entry.get("link") and not LINK_RE.search(str(entry["link"])):
            sys.exit(f"{path}: track {i} has a link I can't read: {entry['link']}")
    return data


def parse_link(link: str):
    """Return (kind, id) from a Spotify URL or URI."""
    m = LINK_RE.search(str(link))
    return (m.group(1), m.group(2)) if m else (None, None)


def label(entry: dict) -> str:
    parts = [entry.get("artist"), entry.get("title")]
    return " – ".join(p for p in parts if p) or entry.get("link", "?")


# ---------------------------------------------------------------- auth

def _pkce_login(client_id: str) -> dict:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    })
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            for key in ("code", "state", "error"):
                result[key] = q.get(key, [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Signed in. You can close this tab.</h2>")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 8888), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    print("Opening your browser to approve access...")
    print(f"If it doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)
    thread.join(timeout=300)
    server.server_close()

    if result.get("error") or not result.get("code"):
        sys.exit(f"Sign-in failed: {result.get('error') or 'no response within 5 minutes'}")
    if result.get("state") != state:
        sys.exit("Sign-in failed: state mismatch")

    resp = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "code_verifier": verifier,
    }, timeout=30)
    if not resp.ok:
        sys.exit(f"Token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _save_token(tok: dict):
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tok))
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def get_access_token(client_id: str, force_login: bool = False) -> str:
    tok = None
    if TOKEN_FILE.exists() and not force_login:
        try:
            tok = json.loads(TOKEN_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            tok = None
        # Re-login if the cached token lacks scopes we now need.
        if tok and not set(SCOPES.split()) <= set(tok.get("scope", "").split()):
            tok = None

    if tok and tok.get("expires_at", 0) > time.time():
        return tok["access_token"]

    if tok and tok.get("refresh_token"):
        resp = requests.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": client_id,
        }, timeout=30)
        if resp.ok:
            new = resp.json()
            new.setdefault("refresh_token", tok["refresh_token"])
            new.setdefault("scope", tok.get("scope", ""))
            _save_token(new)
            return new["access_token"]

    new = _pkce_login(client_id)
    _save_token(new)
    return new["access_token"]


# ---------------------------------------------------------------- API client

class Spotify:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        self._me = None

    def call(self, method: str, url: str, **kw):
        if not url.startswith("http"):
            url = API + url
        for _ in range(6):
            r = self.s.request(method, url, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "2")) + 1)
                continue
            if r.status_code >= 500:
                time.sleep(2)
                continue
            if not r.ok:
                raise SpotifyError(f"{method} {url} → {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}
        raise SpotifyError(f"{method} {url} kept failing; try again later")

    def paged(self, url: str):
        while url:
            page = self.call("GET", url)
            yield from page.get("items", [])
            url = page.get("next")

    def me(self) -> dict:
        if self._me is None:
            self._me = self.call("GET", "/me")
        return self._me

    def search_track(self, artist: str, title: str):
        q = f'track:"{title}" artist:"{artist}"'
        res = self.call("GET", "/search", params={"q": q, "type": "track", "limit": 5})
        items = res.get("tracks", {}).get("items", [])
        if not items:  # looser fallback
            res = self.call("GET", "/search",
                            params={"q": f"{artist} {title}", "type": "track", "limit": 5})
            items = res.get("tracks", {}).get("items", [])
        return items[0] if items else None

    def playlist_tracks(self, playlist_id: str) -> list:
        out = []
        for entry in self.paged(f"/playlists/{playlist_id}/items?limit=50"):
            obj = entry.get("item") or entry.get("track")  # renamed in Feb 2026
            if obj and str(obj.get("uri", "")).startswith("spotify:track:"):
                out.append(obj)
        return out

    def playlist_uris(self, playlist_id: str) -> list:
        return [t["uri"] for t in self.playlist_tracks(playlist_id)]

    def album_uris(self, album_id: str, discs=None, numbers=None) -> list:
        return [t["uri"] for t in self.paged(f"/albums/{album_id}/tracks?limit=50")
                if (not discs or t.get("disc_number", 1) in discs)
                and (not numbers or t.get("track_number") in numbers)]

    def my_playlists(self, include_followed: bool = False) -> list:
        me_id = self.me()["id"]
        out = []
        for p in self.paged("/me/playlists?limit=50"):
            if p is None:
                continue
            if include_followed or p.get("owner", {}).get("id") == me_id:
                out.append(p)
        return out


class SpotifyError(Exception):
    pass


def count_items(p: dict):
    box = p.get("items") if isinstance(p.get("items"), dict) else p.get("tracks")
    return box.get("total") if isinstance(box, dict) else None


def playlist_url(p: dict) -> str:
    return p.get("external_urls", {}).get("spotify") or f"https://open.spotify.com/playlist/{p['id']}"


# ---------------------------------------------------------------- commands

def resolve(sp, entries, quiet=False):
    """Turn tracklist entries into [(track uri, entry)], in order, plus problems."""
    say = (lambda *a, **k: None) if quiet else print
    pairs, problems = [], []
    for i, e in enumerate(entries, 1):
        kind, sid = parse_link(e.get("link", ""))
        try:
            if kind == "track":
                pairs.append((f"spotify:track:{sid}", e))
            elif kind == "playlist":
                found = sp.playlist_uris(sid)
                say(f"  {i:>3}. {label(e)}: {len(found)} tracks from playlist")
                if not found:
                    problems.append(f"{i}. {label(e)}: playlist empty or not readable "
                                    f"(only your own playlists can be read)")
                pairs.extend((u, e) for u in found)
            elif kind == "album":
                found = sp.album_uris(sid, e.get("discs"), e.get("track_numbers"))
                say(f"  {i:>3}. {label(e)}: {len(found)} tracks from album")
                pairs.extend((u, e) for u in found)
            else:
                hit = sp.search_track(e["artist"], e["title"])
                if hit:
                    who = ", ".join(a["name"] for a in hit.get("artists", []))
                    say(f"  {i:>3}. {label(e)}: matched '{hit['name']}' by {who}")
                    pairs.append((hit["uri"], e))
                else:
                    problems.append(f"{i}. {label(e)}: not found by search")
        except SpotifyError as err:
            problems.append(f"{i}. {label(e)}: {err}")
    return pairs, problems


def cmd_create(args):
    data = load_tracklist(args.file)
    name = args.name or data.get("name")
    if not name:
        sys.exit("No playlist name: add 'name:' to the file or pass --name")
    description = args.description if args.description is not None else data.get("description", "")
    public = args.public or bool(data.get("public", False))

    entries = [e for e in data["tracks"] if not e.get("skip")]
    skipped = len(data["tracks"]) - len(entries)

    if args.dry_run:
        print(f"Would create '{name}' ({'public' if public else 'private'}) with:")
        for i, e in enumerate(entries, 1):
            kind, _ = parse_link(e.get("link", ""))
            how = {"track": "track", "playlist": "all tracks of playlist",
                   "album": "album tracks" + (f", disc(s) {e['discs']}" if e.get("discs") else "")
                            + (f", track(s) {e['track_numbers']}" if e.get("track_numbers") else "")
                   }.get(kind, "search by artist + title")
            print(f"  {i:>3}. {label(e)}  [{how}]")
        if skipped:
            print(f"  ({skipped} entries marked skip)")
        return

    sp = Spotify(get_access_token(client_id()))

    target = None
    clash = [p for p in sp.my_playlists() if p.get("name") == name]
    if clash and args.replace:
        if len(clash) > 1:
            sys.exit(f"{len(clash)} playlists are called '{name}'; delete the extras first "
                     f"(delete \"{name}\" shows their IDs).")
        target = clash[0]
        print(f"Warning: '{name}' already exists ({n if (n := count_items(target)) is not None else '?'} tracks); "
              f"its tracks will be replaced.")
    elif clash and args.allow_duplicate_name:
        print(f"Warning: you already have {len(clash)} playlist(s) called '{name}'; "
              f"creating another.")
    elif clash:
        sys.exit(f"You already have a playlist called '{name}'. Pass --replace to overwrite "
                 f"its tracks, delete it first, choose --name, or pass --allow-duplicate-name.")

    print("Resolving tracks...")
    pairs, problems = resolve(sp, entries)
    uris = [u for u, _ in pairs]

    if not uris:
        sys.exit("Nothing to add; no playlist created.")

    details = {"name": name, "description": description, "public": public}
    if target:
        playlist = target
        sp.call("PUT", f"/playlists/{playlist['id']}", json=details)
    else:
        playlist = sp.call("POST", "/me/playlists", json=details)
    for start in range(0, len(uris), 100):   # API limit: 100 per request, order kept
        # PUT on the first batch wipes the old tracks when replacing.
        method = "PUT" if target and start == 0 else "POST"
        sp.call(method, f"/playlists/{playlist['id']}/items",
                json={"uris": uris[start:start + 100]})

    print(f"\n{'Replaced tracks in' if target else 'Created'} '{name}' with {len(uris)} tracks:"
          f"\n{playlist_url(playlist)}")
    if skipped:
        print(f"{skipped} entries were marked skip.")
    if problems:
        print("\nNot added:")
        for p in problems:
            print(f"  {p}")


def cmd_list(args):
    sp = Spotify(get_access_token(client_id()))
    playlists = sp.my_playlists(include_followed=args.all)
    if args.filter:
        playlists = [p for p in playlists if args.filter.lower() in p.get("name", "").lower()]
    if args.json:
        print(json.dumps([{
            "name": p.get("name"), "id": p["id"], "tracks": count_items(p),
            "public": p.get("public"), "owner": p.get("owner", {}).get("display_name"),
            "url": playlist_url(p)} for p in playlists], indent=2))
        return
    if not playlists:
        print("No playlists found.")
        return
    width = min(max(len(p.get("name", "")) for p in playlists), 50)
    for p in playlists:
        n = count_items(p)
        vis = "public " if p.get("public") else "private"
        print(f"{p.get('name', '')[:50]:<{width}}  {str(n) if n is not None else '?':>4} tracks  "
              f"{vis}  {p['id']}")


def find_playlists(sp, target: str) -> list:
    """Playlists in your library matching a name, ID, URI or link."""
    kind, sid = parse_link(target)
    if kind != "playlist" and re.fullmatch(r"[A-Za-z0-9]{22}", target):
        kind, sid = "playlist", target
    if kind == "playlist":
        return [p for p in sp.my_playlists(include_followed=True) if p["id"] == sid] \
            or [{"id": sid, "name": sid}]
    matches = [p for p in sp.my_playlists(include_followed=True) if p.get("name") == target]
    if not matches:
        sys.exit(f"No playlist named '{target}'. Run 'list' to see names and IDs.")
    return matches


def exit_if_ambiguous(matches: list, target: str, hint: str):
    if len(matches) > 1:
        print(f"{len(matches)} playlists are called '{target}':")
        for p in matches:
            print(f"  {p['id']}  {count_items(p)} tracks")
        sys.exit(hint)


def cmd_delete(args):
    sp = Spotify(get_access_token(client_id()))
    matches = find_playlists(sp, args.target)
    if not args.all_matches:
        exit_if_ambiguous(matches, args.target,
                          "Delete one by ID, or pass --all-matches to remove them all.")

    for p in matches:
        if not args.yes:
            answer = input(f"Remove '{p.get('name')}' ({p['id']}) from your library? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                print("Skipped.")
                continue
        sp.call("DELETE", "/me/library", params={"uris": f"spotify:playlist:{p['id']}"})
        print(f"Removed '{p.get('name')}'.")
    print("Note: Spotify has no hard delete; a removed playlist you own can be "
          "recovered for a while from your account page (Recover playlists).")


def fmt_ms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def track_artists(t: dict) -> str:
    return ", ".join(a["name"] for a in t.get("artists", []))


def album_year(t: dict) -> str:
    return (t.get("album", {}).get("release_date") or "")[:4]


def compare_tracks(pairs: list, live: list) -> list:
    """Line up the file's tracks ([(uri, entry)]) with the playlist's, position by position."""
    rows = []
    for i in range(max(len(pairs), len(live))):
        want = pairs[i] if i < len(pairs) else None
        have = live[i] if i < len(live) else None
        rows.append({
            "position": i + 1,
            "section": (want[1].get("section") or "(no section)") if want else "(extra on Spotify)",
            "ok": bool(want and have and want[0] == have["uri"]),
            "file": label(want[1]) if want else None,
            "spotify": f"{track_artists(have)} – {have.get('name')}" if have else None,
            "duration_ms": have.get("duration_ms", 0) if have else 0,
        })
    return rows


def playlist_stats(tracks: list) -> dict:
    """Summary numbers for a non-empty list of Spotify track objects."""
    total = sum(t.get("duration_ms", 0) for t in tracks)
    years = sorted(int(y) for t in tracks if (y := album_year(t)).isdigit())
    return {
        "total_ms": total,
        "average_ms": total // len(tracks),
        "shortest": min(tracks, key=lambda t: t.get("duration_ms", 0)),
        "longest": max(tracks, key=lambda t: t.get("duration_ms", 0)),
        "artists": collections.Counter(a["name"] for t in tracks for a in t.get("artists", [])),
        "years": years,
        "decades": collections.Counter(f"{y // 10 * 10}s" for y in years),
    }


def cmd_check(args):
    data = load_tracklist(args.file)
    name = args.name or data.get("name")
    if not name:
        sys.exit("No playlist name: add 'name:' to the file or pass --name")
    sp = Spotify(get_access_token(client_id()))
    found = [p for p in sp.my_playlists() if p.get("name") == name]
    if len(found) != 1:
        sys.exit(f"Expected one playlist you own called '{name}', found {len(found)}.")
    pairs, problems = resolve(sp, [e for e in data["tracks"] if not e.get("skip")], quiet=True)
    live = sp.playlist_tracks(found[0]["id"])
    rows = compare_tracks(pairs, live)
    mismatches = sum(not r["ok"] for r in rows)

    for sec, group in itertools.groupby(rows, key=lambda r: r["section"]):
        group = list(group)
        print(f"\n{sec}  ({len(group)} tracks, {fmt_ms(sum(r['duration_ms'] for r in group))})")
        for r in group:
            if r["ok"]:
                print(f"   {r['position']:>3}. {r['spotify']:<70} {fmt_ms(r['duration_ms']):>8}")
            else:
                print(f" ✗ {r['position']:>3}. file:    {r['file'] or '(nothing)'}\n"
                      f"         Spotify: {r['spotify'] or '(nothing)'}")
    total = sum(r["duration_ms"] for r in rows)

    print(f"\n{len(live)} tracks on Spotify, {fmt_ms(total)} total. {playlist_url(found[0])}")
    for p in problems:
        print(f"Could not resolve: {p}")
    if mismatches or problems:
        sys.exit(f"MISMATCH: {mismatches} position(s) differ from {args.file}.")
    print(f"OK: Spotify playlist matches {args.file}.")


def cmd_info(args):
    if args.file and not args.set_description:
        sys.exit("--file is only used with --set-description.")
    if args.file:
        load_tracklist(args.file)          # fail early on a bad file, before any API calls
    sp = Spotify(get_access_token(client_id()))
    matches = find_playlists(sp, args.target)
    exit_if_ambiguous(matches, args.target, "Pass the ID or link of the one you want.")
    pl = matches[0]
    try:   # fuller details (description etc.) when the API allows it
        pl = {**pl, **sp.call("GET", f"/playlists/{pl['id']}", params={"fields": "name,description,public,owner,external_urls,id"})}
    except SpotifyError:
        pass
    tracks = sp.playlist_tracks(pl["id"])

    artists, year = track_artists, album_year

    print(pl.get("name"))
    print(f"  {playlist_url(pl)}")
    print(f"  Owner:        {pl.get('owner', {}).get('display_name') or pl.get('owner', {}).get('id', '?')}")
    print(f"  Visibility:   {'public' if pl.get('public') else 'private'}")
    if pl.get("description"):
        print(f"  Description:  {pl['description']}")
    if not tracks:
        print("  Tracks:       0 (or not readable: Spotify only lets apps read playlists you own)")
        return

    st = playlist_stats(tracks)
    shortest, longest, by_artist, years, decades = (
        st["shortest"], st["longest"], st["artists"], st["years"], st["decades"])

    print(f"  Tracks:       {len(tracks)} ({len(by_artist)} artists)")
    print(f"  Length:       {fmt_ms(st['total_ms'])} (average {fmt_ms(st['average_ms'])} per track)")
    print(f"  Shortest:     {artists(shortest)} – {shortest['name']} ({fmt_ms(shortest['duration_ms'])})")
    print(f"  Longest:      {artists(longest)} – {longest['name']} ({fmt_ms(longest['duration_ms'])})")
    print(f"  Top artists:  " + ", ".join(f"{a} ({n})" for a, n in by_artist.most_common(5)))
    if years:
        print(f"  Album years:  {years[0]}–{years[-1]}; "
              + ", ".join(f"{d} {n}" for d, n in sorted(decades.items())))
        print("                (from the album on Spotify, so remasters can show a later year)")

    if not args.no_ai:
        ai_summary(pl, tracks, artists, year, args.model)

    print("\nTracklist")
    for i, t in enumerate(tracks, 1):
        name = f"{artists(t)} – {t.get('name')}"
        print(f"  {i:>3}. {name[:68]:<68} {fmt_ms(t.get('duration_ms', 0)):>6}  {year(t)}")

    if args.set_description:
        set_description(sp, pl, tracks, artists, year, args)


AI_MODEL = "claude-opus-5-5"   # default for info's summary; override with --model
# --model accepts a provider name as shorthand for that provider's default model.
AI_DEFAULTS = {"claude": "claude-opus-5-5", "anthropic": "claude-opus-5-5",
               "openai": "gpt-5.5", "gpt": "gpt-5.5"}
AI_SYSTEM = (
    "You write short, specific overviews of music playlists for the person who made them. "
    "Work only from the tracklist you are given; don't invent facts about tracks you aren't sure of. "
    "Write plain text for a terminal: no markdown, headings or bullet points."
)
# Claude models that accept the server-side refusal fallback ("default" mode).
CLAUDE_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class AISkipped(Exception):
    """The AI summary couldn't be produced; the message says why."""


def ai_provider(model: str):
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith(("gpt-", "chatgpt-")) or re.match(r"o\d", model):
        return "openai"
    return None


def claude_text(model: str, prompt: str) -> str:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise AISkipped("add ANTHROPIC_API_KEY to .env")
    try:
        import anthropic
    except ImportError:
        raise AISkipped("pip install anthropic")
    extra = ({"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
             if model in CLAUDE_FALLBACK_MODELS else {})   # re-run on a fallback model if declined
    try:
        resp = anthropic.Anthropic().beta.messages.create(
            model=model, max_tokens=16000, output_config={"effort": "medium"},
            system=AI_SYSTEM, messages=[{"role": "user", "content": prompt}], **extra)
    except anthropic.AuthenticationError:
        raise AISkipped("Anthropic rejected the API key in ANTHROPIC_API_KEY")
    except anthropic.NotFoundError:
        raise AISkipped(f"Anthropic doesn't recognise the model '{model}'")
    except anthropic.RateLimitError:
        raise AISkipped("Anthropic rate limit; try again shortly")
    except anthropic.APIStatusError as err:
        raise AISkipped(f"Anthropic API error {err.status_code}: {err.message}")
    except anthropic.APIConnectionError:
        raise AISkipped("couldn't reach the Anthropic API")
    if resp.stop_reason == "refusal":
        raise AISkipped("the model declined this request")
    return "\n".join(b.text for b in resp.content if b.type == "text").strip()


def openai_text(model: str, prompt: str) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        raise AISkipped("add OPENAI_API_KEY to .env")
    try:
        import openai
    except ImportError:
        raise AISkipped("pip install openai")
    try:
        resp = openai.OpenAI().responses.create(
            model=model, instructions=AI_SYSTEM, input=prompt, max_output_tokens=16000)
    except openai.AuthenticationError:
        raise AISkipped("OpenAI rejected the API key in OPENAI_API_KEY")
    except openai.NotFoundError:
        raise AISkipped(f"OpenAI doesn't recognise the model '{model}' (or your key can't use it)")
    except openai.RateLimitError:
        raise AISkipped("OpenAI rate limit or quota; try again shortly")
    except openai.APIStatusError as err:
        raise AISkipped(f"OpenAI API error {err.status_code}: {err.message}")
    except openai.APIConnectionError:
        raise AISkipped("couldn't reach the OpenAI API")
    if not resp.output_text.strip():
        raise AISkipped(f"OpenAI returned no text (status: {resp.status})")
    return resp.output_text.strip()


def playlist_brief(pl: dict, tracks: list, artists, year) -> str:
    """The playlist as plain text for an AI prompt."""
    lines = [f"{i}. {artists(t)} – {t.get('name')} ({fmt_ms(t.get('duration_ms', 0))}, "
             f"album year {year(t) or '?'})" for i, t in enumerate(tracks, 1)]
    return (f"Playlist: {pl.get('name')}\n"
            f"Description: {pl.get('description') or '(none)'}\n"
            f"{len(tracks)} tracks, {fmt_ms(sum(t.get('duration_ms', 0) for t in tracks))} total, in play order:\n"
            + "\n".join(lines)
            + "\n\nAlbum years can be later than the original release because of remasters and "
              "compilations.\n\n")


def ask_ai(model: str, prompt: str) -> str:
    provider = ai_provider(model)
    if not provider:
        raise AISkipped(f"'{model}' isn't a Claude (claude-...) or OpenAI (gpt-..., o...) model")
    return (claude_text if provider == "anthropic" else openai_text)(model, prompt)


# Spotify doesn't document description rules; these follow the strictest client behaviour
# reported (300 characters in some apps, up to 512 in others), as a single plain-text line.
DESC_MAX = 300


def clean_description(text: str, limit: int = DESC_MAX) -> str:
    """Make text safe for a Spotify playlist description: one plain line, <= limit characters."""
    text = html.unescape(text)
    text = "".join(ch if ch.isprintable() else " " for ch in text)      # newlines, tabs, controls
    text = re.sub(r"</?[A-Za-z][^<>]*>", " ", text).replace("<", "").replace(">", "")  # no HTML
    text = re.sub(r"[*_#`]+", "", text)                                   # no Markdown
    text = re.sub(r"\s+", " ", text).strip().strip("\"'“”‘’").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if cut.endswith((".", "!", "?")):
        end = len(cut) - 1
    if end >= limit // 2:                  # keep whole sentences when that keeps most of it
        return cut[:end + 1]
    return cut[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:–- ") + "…"


def set_description(sp, pl: dict, tracks: list, artists, year, args):
    """Generate a short AI description, confirm it, write it to Spotify (and optionally a file)."""
    if pl.get("owner", {}).get("id") != sp.me()["id"]:
        sys.exit("You can only change the description of playlists you own.")
    model = AI_DEFAULTS.get(args.model.lower(), args.model)
    prompt = (playlist_brief(pl, tracks, artists, year)
              + f"Write a description for this playlist to show on Spotify: one or two sentences, "
                f"at most {DESC_MAX - 50} characters, capturing its sound and journey. Plain text "
                "only: no quotes around it, no hashtags, emoji, line breaks or HTML. Reply with "
                "the description and nothing else.")
    print(f"Asking {model} for a description...", end="", flush=True, file=sys.stderr)
    try:
        text = clean_description(ask_ai(model, prompt))
    except AISkipped as why:
        print("\r\033[K", end="", file=sys.stderr)
        sys.exit(f"Description not changed: {why}.")
    print("\r\033[K", end="", file=sys.stderr)
    if not text:
        sys.exit("Description not changed: the model returned no usable text.")

    print(f"\nNew Spotify description ({len(text)}/{DESC_MAX} characters, by {model}):\n  {text}")
    if pl.get("description"):
        print(f"Replaces:\n  {html.unescape(pl['description'])}")
    if not args.yes:
        answer = input("Set this as the playlist description? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Description not changed.")
            return

    sp.call("PUT", f"/playlists/{pl['id']}", json={"description": text})
    stored = sp.call("GET", f"/playlists/{pl['id']}", params={"fields": "description"}).get("description", "")
    if html.unescape(stored or "") == text:
        print("Description updated on Spotify.")
    else:
        print(f"Description sent, but Spotify now shows:\n  {html.unescape(stored or '(empty)')}\n"
              "(Spotify can take a moment to update, or may have changed the text.)")
    if args.file:
        save_description(args.file, text)
        print(f"Saved it to {args.file} too, so 'create --replace' keeps it.")
    else:
        print("Note: the next 'create --replace' will put back the file's description; "
              "pass --file FILE to save it there as well.")


def save_description(path: str, text: str):
    """Set the top-level description in a YAML or JSON tracklist, keeping YAML comments."""
    p = Path(path)
    if p.suffix.lower() in (".yaml", ".yml"):
        src = p.read_text(encoding="utf-8")
        line = "description: " + json.dumps(text, ensure_ascii=False)   # a JSON string is valid YAML
        if re.search(r"(?m)^description:.*$", src):
            src = re.sub(r"(?m)^description:.*$", lambda _: line, src, count=1)
        elif re.search(r"(?m)^name:.*$", src):
            src = re.sub(r"(?m)^(name:.*)$", lambda m: m.group(1) + "\n" + line, src, count=1)
        else:
            src = line + "\n" + src
        p.write_text(src, encoding="utf-8")
    else:
        data = json.loads(p.read_text(encoding="utf-8"))
        data["description"] = text
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if load_tracklist(path).get("description") != text:
        sys.exit(f"Couldn't save the description to {path} correctly; please check the file.")


def ai_summary(pl: dict, tracks: list, artists, year, model: str = AI_MODEL):
    """Print an AI-written overview of the playlist; never fails the info command."""
    model = AI_DEFAULTS.get(model.lower(), model)
    prompt = (playlist_brief(pl, tracks, artists, year)
              + "In two or three short paragraphs (under 250 words), describe the "
                "playlist's overall character: its genres and eras, how the mood and energy move "
                "from start to finish, a few standout tracks or transitions, and the setting it suits.")
    print(f"Asking {model} for a summary...", end="", flush=True, file=sys.stderr)
    try:
        text = ask_ai(model, prompt)
    except AISkipped as why:
        print("\r\033[K", end="", file=sys.stderr)
        print(f"\nAI summary: skipped ({why}; or pass --no-ai)")
        return
    print("\r\033[K", end="", file=sys.stderr)
    print(f"\nAI summary ({model})")
    for para in text.split("\n"):
        print(textwrap.fill(para, width=90, initial_indent="  ", subsequent_indent="  ") if para.strip() else "")


def load_dotenv(path: Path = Path(__file__).with_name(".env")):
    """Read KEY=VALUE lines from .env next to this script; real env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.removeprefix("export ").strip(), value.strip().strip("\"'"))


def client_id() -> str:
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    if not cid:
        sys.exit("Set SPOTIFY_CLIENT_ID in .env or your shell first (see the top of this file).")
    return cid


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(
        description="Build and manage Spotify playlists from YAML/JSON tracklists.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-V", "--version", action="version",
                    version=f"%(prog)s {__version__} ({__date__}) by {__author__}")
    ap.add_argument("--login", action="store_true",
                    help="ignore the cached token and sign in again")
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create", help="create a playlist from a tracklist file")
    c.add_argument("file", help="YAML (.yaml/.yml) or JSON tracklist")
    c.add_argument("--name", help="playlist name (overrides the file)")
    c.add_argument("--description", help="playlist description (overrides the file)")
    c.add_argument("--public", action="store_true", help="make the playlist public")
    c.add_argument("--dry-run", action="store_true",
                   help="show what would be added without signing in or changing anything")
    c.add_argument("--allow-duplicate-name", action="store_true",
                   help="create even if you already have a playlist with this name")
    c.add_argument("--replace", action="store_true",
                   help="if you own a playlist with this name, replace its tracks (keeps its link)")
    c.set_defaults(func=cmd_create)

    l = sub.add_parser("list", help="list your playlists")
    l.add_argument("--all", action="store_true", help="include playlists you follow but don't own")
    l.add_argument("--filter", help="only names containing this text")
    l.add_argument("--json", action="store_true", help="output JSON")
    l.set_defaults(func=cmd_list)

    d = sub.add_parser("delete", help="remove a playlist from your library")
    d.add_argument("target", help="playlist name, ID, spotify:playlist: URI or open.spotify.com link")
    d.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
    d.add_argument("--all-matches", action="store_true",
                   help="if several playlists share the name, remove all of them")
    d.set_defaults(func=cmd_delete)

    k = sub.add_parser("check", help="check a Spotify playlist matches a tracklist file")
    k.add_argument("file", help="YAML (.yaml/.yml) or JSON tracklist")
    k.add_argument("--name", help="playlist name (overrides the file)")
    k.set_defaults(func=cmd_check)

    n = sub.add_parser("info", help="summary and tracklist of a playlist")
    n.add_argument("target", help="playlist name, ID, spotify:playlist: URI or open.spotify.com link")
    n.add_argument("--no-ai", action="store_true",
                   help="skip the AI-written summary (--set-description still runs)")
    n.add_argument("--set-description", action="store_true",
                   help="write a short AI description (max 300 characters, plain text) to the "
                        "Spotify playlist, after showing it to you")
    n.add_argument("--file", help="with --set-description: also save it to this YAML/JSON tracklist")
    n.add_argument("--yes", "-y", action="store_true", help="with --set-description: don't ask first")
    n.add_argument("--model", default=AI_MODEL,
                   help=f"model for the AI summary and --set-description: 'claude' ({AI_DEFAULTS['claude']}), 'openai' "
                        f"({AI_DEFAULTS['openai']}), or any Claude (claude-...) or OpenAI (gpt-..., o...) "
                        f"model ID (default: {AI_MODEL})")
    n.set_defaults(func=cmd_info)

    args = ap.parse_args()
    load_dotenv()
    if args.login and TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    try:
        args.func(args)
    except SpotifyError as err:
        sys.exit(f"Spotify error: {err}")
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")


if __name__ == "__main__":
    main()
