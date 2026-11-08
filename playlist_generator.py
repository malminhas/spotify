#!/usr/bin/env python3
"""
playlist_generator.py — build Spotify playlists from YAML or JSON tracklists.

Commands
  create FILE      Create a playlist from a YAML/JSON tracklist, in order.
  list             List playlists you own (use --all to include followed ones).
  delete TARGET    Remove a playlist (by name, ID, URI or link) from your library.
  check FILE       Check the Spotify playlist matches the file; prints it by section.

Examples
  python playlist_generator.py create party_2026.yaml
  python playlist_generator.py create party_2026.yaml --dry-run
  python playlist_generator.py create party_2026.yaml --name "Party 2026 v2" --public
  python playlist_generator.py list
  python playlist_generator.py check party_2026.yaml
  python playlist_generator.py delete "Party 2026"
  python playlist_generator.py delete https://open.spotify.com/playlist/1ks0R4jGZ8iGGrpJSzilch --yes

One-off setup
  1. https://developer.spotify.com/dashboard → Create app → choose "Web API".
     Redirect URI: http://127.0.0.1:8888/callback  (must match exactly)
  2. pip install requests pyyaml      (pyyaml only needed for YAML input)
  3. export SPOTIFY_CLIENT_ID=<your app's client id>

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
      section: Arrival              # free text, for your own reference
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
"""

import argparse
import base64
import hashlib
import http.server
import itertools
import json
import os
import re
import secrets
import sys
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


def cmd_delete(args):
    sp = Spotify(get_access_token(client_id()))
    kind, sid = parse_link(args.target)
    if kind == "playlist":
        matches = [p for p in sp.my_playlists(include_followed=True) if p["id"] == sid]
        if not matches:
            matches = [{"id": sid, "name": sid}]
    elif re.fullmatch(r"[A-Za-z0-9]{22}", args.target):
        matches = [p for p in sp.my_playlists(include_followed=True) if p["id"] == args.target] \
            or [{"id": args.target, "name": args.target}]
    else:
        matches = [p for p in sp.my_playlists(include_followed=True)
                   if p.get("name") == args.target]
        if not matches:
            sys.exit(f"No playlist named '{args.target}'. Run 'list' to see names and IDs.")
        if len(matches) > 1 and not args.all_matches:
            print(f"{len(matches)} playlists are called '{args.target}':")
            for p in matches:
                print(f"  {p['id']}  {count_items(p)} tracks")
            sys.exit("Delete one by ID, or pass --all-matches to remove them all.")

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

    def desc(t):
        return f"{', '.join(a['name'] for a in t.get('artists', []))} – {t.get('name')}"

    mismatches, rows = 0, []   # rows: (section, line, ms)
    for i in range(max(len(pairs), len(live))):
        want = pairs[i] if i < len(pairs) else None
        have = live[i] if i < len(live) else None
        sec = want[1].get("section") or "(no section)" if want else "(extra on Spotify)"
        ms = have.get("duration_ms", 0) if have else 0
        if want and have and want[0] == have["uri"]:
            line = f"   {i + 1:>3}. {desc(have):<70} {fmt_ms(ms):>8}"
        else:
            mismatches += 1
            line = (f" ✗ {i + 1:>3}. file:    {label(want[1]) if want else '(nothing)'}\n"
                    f"         Spotify: {desc(have) if have else '(nothing)'}")
        rows.append((sec, line, ms))

    for sec, group in itertools.groupby(rows, key=lambda r: r[0]):
        group = list(group)
        print(f"\n{sec}  ({len(group)} tracks, {fmt_ms(sum(r[2] for r in group))})")
        for _, line, _ in group:
            print(line)
    total = sum(r[2] for r in rows)

    print(f"\n{len(live)} tracks on Spotify, {fmt_ms(total)} total. {playlist_url(found[0])}")
    for p in problems:
        print(f"Could not resolve: {p}")
    if mismatches or problems:
        sys.exit(f"MISMATCH: {mismatches} position(s) differ from {args.file}.")
    print(f"OK: Spotify playlist matches {args.file}.")


def client_id() -> str:
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    if not cid:
        sys.exit("Set SPOTIFY_CLIENT_ID first (see the top of this file for setup).")
    return cid


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(
        description="Build and manage Spotify playlists from YAML/JSON tracklists.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
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

    args = ap.parse_args()
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
