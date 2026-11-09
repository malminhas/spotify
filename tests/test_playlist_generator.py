"""Offline pytest tests for playlist_generator.py: no Spotify, Anthropic or OpenAI calls.

Run from the repo root:  pytest
"""

import json
import sys
import types
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import playlist_generator as pg  # noqa: E402


# ---------------------------------------------------------------- helpers and fixtures

def track(n: int) -> str:
    return f"https://open.spotify.com/track/{n:022d}"


def tracklist(n_tracks: int = 3, album: bool = False) -> dict:
    tracks = [{"title": f"Song {i}", "artist": "Artist", "section": "A" if i < 2 else "B",
               "link": track(i)} for i in range(n_tracks)]
    if album:
        tracks.append({"title": "Album", "link": "https://open.spotify.com/album/" + "a" * 22,
                       "track_numbers": [3]})
    tracks.append({"title": "Later", "artist": "Someone", "skip": True})
    return {"name": "Test Mix", "description": "Old words", "tracks": tracks}


@pytest.fixture
def write(tmp_path):
    """Write a tracklist dict to tmp_path as YAML or JSON (by extension); return its path."""
    def _write(name: str, data: dict, header: str = "") -> str:
        path = tmp_path / name
        if name.endswith(".json"):
            path.write_text(json.dumps(data, indent=2))
        else:
            path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        return str(path)
    return _write


class FakeSpotify(pg.Spotify):
    """Records API calls; owns one playlist called 'Test Mix'."""

    def __init__(self, token=None):
        self._me = {"id": "me"}
        self.calls = []

    def call(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))
        return {"id": "NEW"}

    def my_playlists(self, include_followed=False):
        return [{"id": "OLD", "name": "Test Mix", "owner": {"id": "me"}}]

    def album_uris(self, album_id, discs=None, numbers=None):
        return [track(1000 + i) for i in range(150)]


@pytest.fixture
def run_create(monkeypatch):
    """Run cmd_create against FakeSpotify; return (calls, stdout-capturing result)."""
    def _run(path, **kw):
        sp = FakeSpotify()
        monkeypatch.setattr(pg, "Spotify", lambda token: sp)
        monkeypatch.setattr(pg, "get_access_token", lambda *a: "t")
        monkeypatch.setattr(pg, "client_id", lambda: "c")
        args = types.SimpleNamespace(**{"file": path, "name": None, "description": None,
                                        "public": False, "dry_run": False,
                                        "allow_duplicate_name": False, "replace": False, **kw})
        pg.cmd_create(args)
        return sp.calls
    return _run


# ---------------------------------------------------------------- tracklists

def test_yaml_and_json_load_the_same(write):
    data = tracklist()
    assert pg.load_tracklist(write("t.yaml", data)) == pg.load_tracklist(write("t.json", data))


def test_rejects_unreadable_link(write):
    data = tracklist()
    data["tracks"][0]["link"] = "https://example.com/nope"
    with pytest.raises(SystemExit, match="link I can't read"):
        pg.load_tracklist(write("t.json", data))


@pytest.mark.parametrize("link, expected", [
    ("spotify:album:" + "b" * 22, ("album", "b" * 22)),
    ("https://open.spotify.com/intl-de/track/" + "c" * 22, ("track", "c" * 22)),
    ("nonsense", (None, None)),
])
def test_parse_link(link, expected):
    assert pg.parse_link(link) == expected


# ---------------------------------------------------------------- create --replace

def test_replace_keeps_playlist_and_batches_in_order(write, run_create, capsys):
    calls = run_create(write("t.yaml", tracklist(3, album=True)), replace=True)
    assert calls[0][:2] == ("PUT", "/playlists/OLD")           # details updated in place
    assert calls[1][:2] == ("PUT", "/playlists/OLD/items")     # first batch replaces tracks
    assert len(calls[1][2]["uris"]) == 100
    assert calls[2][:2] == ("POST", "/playlists/OLD/items")    # the rest are appended
    uris = calls[1][2]["uris"] + calls[2][2]["uris"]
    assert len(uris) == 3 + 150                                 # skip entry left out
    assert uris[0] == "spotify:track:" + "0" * 22
    assert "Replaced tracks in 'Test Mix'" in capsys.readouterr().out


def test_existing_name_refused_without_replace(write, run_create):
    with pytest.raises(SystemExit, match="--replace"):
        run_create(write("t.yaml", tracklist()))


# ---------------------------------------------------------------- AI models

@pytest.mark.parametrize("model, provider", [
    ("claude-opus-5-5", "anthropic"), ("gpt-5.5", "openai"), ("o3", "openai"),
    ("chatgpt-4o", "openai"), ("llama-3", None),
])
def test_provider_from_model_name(model, provider):
    assert pg.ai_provider(model) == provider


@pytest.mark.parametrize("name, expected", [
    ("claude", "claude-opus-5-5"), ("Anthropic", "claude-opus-5-5"), ("openai", "gpt-5.5"),
    ("GPT", "gpt-5.5"), ("claude-sonnet-5-5", "claude-sonnet-5-5"), ("gpt-6-sol", "gpt-6-sol"),
])
def test_shortcuts_route_to_models(monkeypatch, name, expected):
    seen = []
    monkeypatch.setattr(pg, "claude_text", lambda m, p: seen.append(m) or "ok")
    monkeypatch.setattr(pg, "openai_text", lambda m, p: seen.append(m) or "ok")
    tracks = [{"name": "Xtal", "duration_ms": 293000, "artists": [{"name": "Aphex Twin"}],
               "album": {"release_date": "1992"}}]
    pg.ai_summary({"name": "P"}, tracks, lambda t: "Aphex Twin", lambda t: "1992", name)
    assert seen == [expected]


def test_missing_key_skips_summary_without_failing(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    pg.ai_summary({"name": "P"}, [], str, str, "openai")
    assert "AI summary: skipped (add OPENAI_API_KEY" in capsys.readouterr().out


# ---------------------------------------------------------------- description cleaning

@pytest.mark.parametrize("raw, clean", [
    ('"Dreamy *psych* into\n\nrave.\tThen <b>ambient</b> calm."',
     "Dreamy psych into rave. Then ambient calm."),
    ("Line1\r\nLine2", "Line1 Line2"),
    ("Rock &amp; roll", "Rock & roll"),          # entities decoded, not double-escaped
    ("a < b > c", "a b c"),                      # stray brackets removed, words kept
    ("   ", ""),
])
def test_clean_description(raw, clean):
    assert pg.clean_description(raw) == clean


def test_clean_description_length_limit():
    words = pg.clean_description(("Word " * 100).strip())
    assert len(words) <= pg.DESC_MAX and words.endswith("…")
    sentences = pg.clean_description(("This is a sentence that is fairly long. " * 10).strip())
    assert len(sentences) <= pg.DESC_MAX and sentences.endswith(".")
    assert len(pg.clean_description("x" * 400)) <= pg.DESC_MAX


# ---------------------------------------------------------------- saving descriptions

def test_save_description_yaml_keeps_comments(write):
    path = write("t.yaml", tracklist(), header="# My comment\n")
    text = 'Dream pop: "hazy" & bright, into rave.'
    pg.save_description(path, text)
    assert Path(path).read_text().startswith("# My comment")
    data = pg.load_tracklist(path)
    assert data["description"] == text and len(data["tracks"]) == 4


def test_save_description_json(write):
    path = write("t.json", tracklist())
    pg.save_description(path, "New words")
    assert pg.load_tracklist(path)["description"] == "New words"


# ---------------------------------------------------------------- info --set-description

AI_TEXT = '"A hazy drift\nfrom dream pop to <i>ambient</i> techno."'
CLEAN = "A hazy drift from dream pop to ambient techno."


class DescSpotify:
    """Fake Spotify for set_description: echoes the PUT description on GET unless 'stored' is set."""

    def __init__(self, stored=None):
        self.calls, self.stored = [], stored

    def me(self):
        return {"id": "me"}

    def call(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))
        if method == "GET":
            return {"description": self.stored if self.stored is not None
                    else self.calls[-2][2]["description"]}
        return {}


@pytest.fixture
def set_desc(monkeypatch):
    """Run set_description with a fake AI reply and a scripted answer to the prompt."""
    def _run(sp, answer="y", owner="me", ai=None, **kw):
        monkeypatch.setattr(pg, "ask_ai", ai or (lambda model, prompt: AI_TEXT))
        monkeypatch.setattr("builtins.input", lambda *_: answer)
        pl = {"id": "P", "name": "Test Mix", "description": "Old words", "owner": {"id": owner}}
        args = types.SimpleNamespace(**{"model": "claude", "yes": False, "file": None, **kw})
        pg.set_description(sp, pl, [], str, str, args)
    return _run


def test_decline_changes_nothing(set_desc, capsys):
    sp = DescSpotify()
    set_desc(sp, answer="n")
    assert "Description not changed." in capsys.readouterr().out
    assert not any(c[0] == "PUT" for c in sp.calls)


def test_accept_writes_clean_text_and_verifies(set_desc, capsys):
    sp = DescSpotify()
    set_desc(sp)
    out = capsys.readouterr().out
    assert sp.calls[0] == ("PUT", "/playlists/P", {"description": CLEAN})
    assert f"({len(CLEAN)}/300 characters" in out
    assert "Description updated on Spotify." in out
    assert "next 'create --replace'" in out                    # warns when there's no --file


def test_html_escaped_readback_still_matches(set_desc, capsys):
    set_desc(DescSpotify(stored=CLEAN.replace(".", "&#x2E;")), yes=True)
    assert "Description updated on Spotify." in capsys.readouterr().out


def test_changed_readback_is_reported(set_desc, capsys):
    set_desc(DescSpotify(stored="something else"), yes=True)
    assert "Spotify now shows" in capsys.readouterr().out


def test_set_description_updates_file(set_desc, write):
    path = write("t.yaml", tracklist())
    set_desc(DescSpotify(), yes=True, file=path)
    assert pg.load_tracklist(path)["description"] == CLEAN


def test_only_own_playlists(set_desc):
    with pytest.raises(SystemExit, match="only change the description of playlists you own"):
        set_desc(DescSpotify(), owner="someone-else", yes=True)


def test_ai_failure_exits_with_reason(set_desc):
    def fail(model, prompt):
        raise pg.AISkipped("add OPENAI_API_KEY to .env")
    with pytest.raises(SystemExit, match="Description not changed: add OPENAI_API_KEY"):
        set_desc(DescSpotify(), ai=fail, yes=True)


# ---------------------------------------------------------------- version

def test_version_flag(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["playlist_generator.py", "-V"])
    with pytest.raises(SystemExit) as exit_info:
        pg.main()
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == (
        f"playlist_generator.py {pg.__version__} ({pg.__date__}) by {pg.__author__}")


# ---------------------------------------------------------------- list, delete, dry-run, check, info output

def track_obj(n: int, artist: str = "Artist", ms: int = 200_000, year: str = "1991") -> dict:
    tid = f"{n:022d}"
    return {"id": tid, "uri": f"spotify:track:{tid}", "name": f"Song {n}", "duration_ms": ms,
            "artists": [{"name": artist}], "album": {"name": "Album", "release_date": year}}


def pl_obj(pid: str, name: str, owner: str = "me", tracks: int = 3, public: bool = False) -> dict:
    return {"id": pid * 22 if len(pid) == 1 else pid, "name": name, "owner": {"id": owner, "display_name": owner},
            "public": public, "items": {"total": tracks}}


class CliSpotify:
    """Fake Spotify for the list/delete/check/info commands; records every call."""

    def __init__(self, playlists=None, live=None):
        self.playlists = playlists if playlists is not None else [pl_obj("A", "Test Mix")]
        self.live = live if live is not None else [track_obj(i) for i in range(3)]
        self.calls = []

    def me(self):
        return {"id": "me"}

    def my_playlists(self, include_followed=False):
        return [p for p in self.playlists if include_followed or p["owner"]["id"] == "me"]

    def playlist_tracks(self, playlist_id):
        return self.live

    def album_uris(self, album_id, discs=None, numbers=None):
        return []

    def call(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return {}


@pytest.fixture
def cli(monkeypatch):
    """Point the CLI at a CliSpotify; returns a function that sets it up and returns it."""
    def _use(sp=None):
        sp = sp or CliSpotify()
        monkeypatch.setattr(pg, "Spotify", lambda token: sp)
        monkeypatch.setattr(pg, "get_access_token", lambda *a: "t")
        monkeypatch.setattr(pg, "client_id", lambda: "c")
        return sp
    return _use


def ns(**kw):
    return types.SimpleNamespace(**kw)


def test_list_owned_filter_all_and_json(cli, capsys):
    cli(CliSpotify(playlists=[pl_obj("A", "Test Mix", tracks=3), pl_obj("B", "Party", public=True),
                              pl_obj("C", "Followed", owner="someone")]))
    pg.cmd_list(ns(all=False, filter=None, json=False))
    out = capsys.readouterr().out
    assert "Test Mix" in out and "Party" in out and "Followed" not in out
    assert "3 tracks  private" in out and "public " in out
    pg.cmd_list(ns(all=True, filter="foll", json=True))
    data = json.loads(capsys.readouterr().out)
    assert [p["name"] for p in data] == ["Followed"] and data[0]["owner"] == "someone"


def test_list_empty(cli, capsys):
    cli(CliSpotify(playlists=[]))
    pg.cmd_list(ns(all=False, filter=None, json=False))
    assert "No playlists found." in capsys.readouterr().out


def delete_args(target, yes=False, all_matches=False):
    return ns(target=target, yes=yes, all_matches=all_matches)


def deleted(sp):
    return [kw["params"]["uris"] for m, u, kw in sp.calls if m == "DELETE" and u == "/me/library"]


def test_delete_by_name_asks_first(cli, monkeypatch, capsys):
    sp = cli()
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    pg.cmd_delete(delete_args("Test Mix"))
    assert deleted(sp) == [] and "Skipped." in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    pg.cmd_delete(delete_args("Test Mix"))
    assert deleted(sp) == ["spotify:playlist:" + "A" * 22]


def test_delete_by_link_and_id_with_yes(cli):
    sp = cli()
    pg.cmd_delete(delete_args("https://open.spotify.com/playlist/" + "A" * 22, yes=True))
    pg.cmd_delete(delete_args("A" * 22, yes=True))
    assert deleted(sp) == ["spotify:playlist:" + "A" * 22] * 2


def test_delete_shared_name_needs_all_matches(cli):
    sp = cli(CliSpotify(playlists=[pl_obj("A", "Dup"), pl_obj("B", "Dup")]))
    with pytest.raises(SystemExit, match="--all-matches"):
        pg.cmd_delete(delete_args("Dup", yes=True))
    assert deleted(sp) == []
    pg.cmd_delete(delete_args("Dup", yes=True, all_matches=True))
    assert len(deleted(sp)) == 2


def test_delete_unknown_name(cli):
    cli()
    with pytest.raises(SystemExit, match="No playlist named 'Nope'"):
        pg.cmd_delete(delete_args("Nope", yes=True))


def test_dry_run_needs_no_sign_in(write, monkeypatch, capsys):
    def no_sign_in(*a):
        raise AssertionError("dry run must not sign in")
    monkeypatch.setattr(pg, "get_access_token", no_sign_in)
    data = tracklist(2, album=True)
    data["tracks"].insert(0, {"title": "Unlinked", "artist": "Someone"})
    pg.cmd_create(ns(file=write("t.yaml", data), name=None, description=None, public=False,
                     dry_run=True, allow_duplicate_name=False, replace=False))
    out = capsys.readouterr().out
    assert "Would create 'Test Mix' (private) with:" in out
    assert "Someone – Unlinked  [search by artist + title]" in out
    assert "Artist – Song 0  [track]" in out
    assert "album tracks, track(s) [3]" in out and "(1 entries marked skip)" in out


def check_file(write, order=(0, 1, 2)):
    return write("t.yaml", {"name": "Test Mix", "tracks": [
        {"title": f"Song {i}", "artist": "Artist", "section": "Intro" if n < 2 else "Peak", "link": track(i)}
        for n, i in enumerate(order)]})


def test_check_ok_prints_sections(cli, write, capsys):
    cli()
    pg.cmd_check(ns(file=check_file(write), name=None))
    out = capsys.readouterr().out
    assert "Intro  (2 tracks, 6:40)" in out and "Peak  (1 tracks, 3:20)" in out
    assert "OK: Spotify playlist matches" in out and "✗" not in out


def test_check_mismatch_exits_with_details(cli, write, capsys):
    cli()
    with pytest.raises(SystemExit, match="MISMATCH: 2 position"):
        pg.cmd_check(ns(file=check_file(write, order=(1, 0, 2)), name=None))
    out = capsys.readouterr().out
    assert out.count("✗") == 2 and "file:    Artist – Song 1" in out and "Spotify: Artist – Song 0" in out


def test_check_needs_the_playlist(cli, write):
    cli(CliSpotify(playlists=[]))
    with pytest.raises(SystemExit, match="found 0"):
        pg.cmd_check(ns(file=check_file(write), name=None))


def test_info_prints_stats_and_tracklist(cli, capsys):
    cli(CliSpotify(live=[track_obj(0, "Orbital", 180_000, "1991"), track_obj(1, "Orbital", 600_000, "1993"),
                         track_obj(2, "Aphex Twin", 300_000, "1975")]))
    pg.cmd_info(ns(target="Test Mix", no_ai=True, set_description=False, file=None, yes=False, model="claude"))
    out = capsys.readouterr().out
    assert "Tracks:       3 (2 artists)" in out
    assert "Length:       18:00 (average 6:00 per track)" in out
    assert "Shortest:     Orbital – Song 0 (3:00)" in out and "Longest:      Orbital – Song 1 (10:00)" in out
    assert "Top artists:  Orbital (2), Aphex Twin (1)" in out
    assert "Album years:  1975–1993; 1970s 1, 1990s 2" in out
    assert "AI summary" not in out                               # --no-ai
    assert "    3. Aphex Twin – Song 2" in out


def test_info_file_needs_set_description():
    with pytest.raises(SystemExit, match="--file is only used with --set-description"):
        pg.cmd_info(ns(target="x", no_ai=True, set_description=False, file="t.yaml", yes=False, model="claude"))


# ---------------------------------------------------------------- .env loading

def test_load_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nPG_TEST_A=plain\nexport PG_TEST_B="quoted value"\nPG_TEST_C=from-file\n\nnot a setting\n')
    for k in ("PG_TEST_A", "PG_TEST_B"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PG_TEST_C", "from-shell")
    pg.load_dotenv(env)
    import os
    assert os.environ["PG_TEST_A"] == "plain"
    assert os.environ["PG_TEST_B"] == "quoted value"            # quotes and 'export ' handled
    assert os.environ["PG_TEST_C"] == "from-shell"              # the shell wins over .env
    for k in ("PG_TEST_A", "PG_TEST_B"):
        monkeypatch.delenv(k)


def test_load_dotenv_missing_file_is_fine(tmp_path):
    pg.load_dotenv(tmp_path / "nope.env")
