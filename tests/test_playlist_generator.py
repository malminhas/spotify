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
