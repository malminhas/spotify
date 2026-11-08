"""Offline tests for playlist_generator.py: no Spotify, Anthropic or OpenAI calls.

Run from the repo root:  python -m unittest discover tests
"""

import builtins
import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import playlist_generator as pg  # noqa: E402


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


def quietly(fn, *a, **kw):
    """Run fn with stdout/stderr captured; return (stdout, exit code or None)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            fn(*a, **kw)
            code = None
        except SystemExit as e:
            code = e.code
    return out.getvalue(), code


class TempFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name: str, data: dict, header: str = "") -> str:
        path = Path(self.dir.name) / name
        if name.endswith(".json"):
            path.write_text(json.dumps(data, indent=2))
        else:
            import yaml
            path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        return str(path)


class LoadTracklist(TempFiles):
    def test_yaml_and_json_load_the_same(self):
        data = tracklist()
        self.assertEqual(pg.load_tracklist(self.write("t.yaml", data)),
                         pg.load_tracklist(self.write("t.json", data)))

    def test_rejects_unreadable_link(self):
        data = tracklist()
        data["tracks"][0]["link"] = "https://example.com/nope"
        _, code = quietly(pg.load_tracklist, self.write("t.json", data))
        self.assertIn("link I can't read", str(code))

    def test_parse_link(self):
        self.assertEqual(pg.parse_link("spotify:album:" + "b" * 22), ("album", "b" * 22))
        self.assertEqual(pg.parse_link("nonsense"), (None, None))


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


class CreateReplace(TempFiles):
    def run_create(self, path, **kw):
        sp = FakeSpotify()
        args = types.SimpleNamespace(**{"file": path, "name": None, "description": None,
                                        "public": False, "dry_run": False,
                                        "allow_duplicate_name": False, "replace": False, **kw})
        with mock.patch.object(pg, "Spotify", lambda token: sp), \
             mock.patch.object(pg, "get_access_token", lambda *a: "t"), \
             mock.patch.object(pg, "client_id", lambda: "c"):
            out, code = quietly(pg.cmd_create, args)
        return sp.calls, out, code

    def test_replace_keeps_playlist_and_batches_in_order(self):
        calls, out, _ = self.run_create(self.write("t.yaml", tracklist(3, album=True)), replace=True)
        self.assertEqual(calls[0][:2], ("PUT", "/playlists/OLD"))           # details updated
        self.assertEqual(calls[1][:2], ("PUT", "/playlists/OLD/items"))     # first batch replaces
        self.assertEqual(len(calls[1][2]["uris"]), 100)
        self.assertEqual(calls[2][:2], ("POST", "/playlists/OLD/items"))    # rest appended
        uris = calls[1][2]["uris"] + calls[2][2]["uris"]
        self.assertEqual(len(uris), 3 + 150)                                 # skip entry left out
        self.assertEqual(uris[0], "spotify:track:" + "0" * 22)
        self.assertIn("Replaced tracks in 'Test Mix'", out)

    def test_existing_name_refused_without_replace(self):
        calls, _, code = self.run_create(self.write("t.yaml", tracklist()))
        self.assertIn("--replace", str(code))
        self.assertEqual(calls, [])                                          # nothing changed


class AIModels(unittest.TestCase):
    def test_provider_from_model_name(self):
        for model, provider in [("claude-opus-5-5", "anthropic"), ("gpt-5.5", "openai"),
                                ("o3", "openai"), ("chatgpt-4o", "openai"), ("llama-3", None)]:
            self.assertEqual(pg.ai_provider(model), provider, model)

    def test_shortcuts_route_to_default_models(self):
        seen = []
        tracks = [{"name": "Xtal", "duration_ms": 293000, "artists": [{"name": "Aphex Twin"}],
                   "album": {"release_date": "1992"}}]
        with mock.patch.object(pg, "claude_text", lambda m, p: seen.append(m) or "ok"), \
             mock.patch.object(pg, "openai_text", lambda m, p: seen.append(m) or "ok"):
            for name in ["claude", "Anthropic", "openai", "GPT", "claude-sonnet-5-5"]:
                quietly(pg.ai_summary, {"name": "P"}, tracks, lambda t: "Aphex Twin",
                        lambda t: "1992", name)
        self.assertEqual(seen, ["claude-opus-5-5", "claude-opus-5-5", "gpt-5.5", "gpt-5.5",
                                "claude-sonnet-5-5"])

    def test_missing_key_skips_without_failing(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            out, code = quietly(pg.ai_summary, {"name": "P"}, [], str, str, "openai")
        self.assertIsNone(code)
        self.assertIn("AI summary: skipped (add OPENAI_API_KEY", out)


class CleanDescription(unittest.TestCase):
    c = staticmethod(pg.clean_description)

    def test_plain_single_line(self):
        self.assertEqual(self.c('"Dreamy *psych* into\n\nrave.\tThen <b>ambient</b> calm."'),
                         "Dreamy psych into rave. Then ambient calm.")
        self.assertEqual(self.c("Line1\r\nLine2"), "Line1 Line2")

    def test_html_entities_and_brackets(self):
        self.assertEqual(self.c("Rock &amp; roll"), "Rock & roll")
        self.assertEqual(self.c("a < b > c"), "a b c")            # stray brackets, words kept

    def test_length_limit(self):
        words = self.c(("Word " * 100).strip())
        self.assertLessEqual(len(words), pg.DESC_MAX)
        self.assertTrue(words.endswith("…"))
        sentences = self.c(("This is a sentence that is fairly long. " * 10).strip())
        self.assertLessEqual(len(sentences), pg.DESC_MAX)
        self.assertTrue(sentences.endswith("."))
        self.assertLessEqual(len(self.c("x" * 400)), pg.DESC_MAX)

    def test_empty(self):
        self.assertEqual(self.c("   "), "")


class SaveDescription(TempFiles):
    def test_yaml_keeps_comments_and_round_trips(self):
        path = self.write("t.yaml", tracklist(), header="# My comment\n")
        text = 'Dream pop: "hazy" & bright, into rave.'
        pg.save_description(path, text)
        self.assertTrue(Path(path).read_text().startswith("# My comment"))
        data = pg.load_tracklist(path)
        self.assertEqual(data["description"], text)
        self.assertEqual(len(data["tracks"]), 4)

    def test_json(self):
        path = self.write("t.json", tracklist())
        pg.save_description(path, "New words")
        self.assertEqual(pg.load_tracklist(path)["description"], "New words")


class SetDescription(TempFiles):
    AI_TEXT = '"A hazy drift\nfrom dream pop to <i>ambient</i> techno."'
    CLEAN = "A hazy drift from dream pop to ambient techno."

    class Sp:
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

    def run_set(self, sp, answer="y", owner="me", ai=None, **kw):
        pl = {"id": "P", "name": "Test Mix", "description": "Old words", "owner": {"id": owner}}
        args = types.SimpleNamespace(**{"model": "claude", "yes": False, "file": None, **kw})
        ask = ai or (lambda model, prompt: self.AI_TEXT)
        with mock.patch.object(pg, "ask_ai", ask), \
             mock.patch.object(builtins, "input", lambda *_: answer):
            return quietly(pg.set_description, sp, pl, [], str, str, args)

    def test_decline_changes_nothing(self):
        sp = self.Sp()
        out, _ = self.run_set(sp, answer="n")
        self.assertIn("Description not changed.", out)
        self.assertFalse(any(c[0] == "PUT" for c in sp.calls))

    def test_accept_writes_clean_text_and_verifies(self):
        sp = self.Sp()
        out, _ = self.run_set(sp)
        self.assertEqual(sp.calls[0], ("PUT", "/playlists/P", {"description": self.CLEAN}))
        self.assertIn(f"({len(self.CLEAN)}/300 characters", out)
        self.assertIn("Description updated on Spotify.", out)
        self.assertIn("next 'create --replace'", out)             # warns without --file

    def test_html_escaped_readback_still_matches(self):
        out, _ = self.run_set(self.Sp(stored=self.CLEAN.replace(".", "&#x2E;")), yes=True)
        self.assertIn("Description updated on Spotify.", out)

    def test_changed_readback_is_reported(self):
        out, _ = self.run_set(self.Sp(stored="something else"), yes=True)
        self.assertIn("Spotify now shows", out)

    def test_file_is_updated(self):
        path = self.write("t.yaml", tracklist())
        self.run_set(self.Sp(), yes=True, file=path)
        self.assertEqual(pg.load_tracklist(path)["description"], self.CLEAN)

    def test_only_own_playlists(self):
        _, code = self.run_set(self.Sp(), owner="someone-else", yes=True)
        self.assertIn("only change the description of playlists you own", str(code))

    def test_ai_failure_exits_with_reason(self):
        def fail(model, prompt):
            raise pg.AISkipped("add OPENAI_API_KEY to .env")
        _, code = self.run_set(self.Sp(), ai=fail, yes=True)
        self.assertIn("Description not changed: add OPENAI_API_KEY", str(code))


class Version(unittest.TestCase):
    def test_version_flag(self):
        with mock.patch.object(sys, "argv", ["playlist_generator.py", "-V"]):
            out, code = quietly(pg.main)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(),
                         f"playlist_generator.py {pg.__version__} ({pg.__date__}) by {pg.__author__}")


if __name__ == "__main__":
    unittest.main()
