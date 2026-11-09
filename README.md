# spotify

Various Spotify experiments:

- **[`playlist_generator.py`](playlist_generator.py)**: a command-line tool that builds Spotify playlists from YAML or JSON tracklists, then lists, checks, summarises and describes them (with optional Claude- or OpenAI-written overviews and Spotify descriptions), and deletes them.
- **[`mcp_server.py`](mcp_server.py)**: a local MCP server so Claude Code and Claude Desktop can look at your playlists, check them against tracklists, search Spotify and play music.
- **[`spotify_playbook.ipynb`](spotify_playbook.ipynb)**: a Jupyter notebook that explores a playlist with `spotipy` and analyses your Spotify streaming history.

---

## Playlist generator

**Version 1.2.1** (2026-11-09). Run `python playlist_generator.py -V` to see the installed version; release notes are in the script's header.

`playlist_generator.py` takes a tracklist file (YAML or JSON) and creates a Spotify playlist with the tracks in file order. Tracklists are local files and are git-ignored (see [Tracklists stay local](#tracklists-stay-local)).

It targets the Spotify Web API as it stands after the February 2026 Development Mode changes (`POST /me/playlists`, `/playlists/{id}/items`, `DELETE /me/library`). Older libraries such as `spotipy` may fail on these endpoints. **The Spotify account that owns the developer app needs Premium.**

### Setup (once)

1. Go to the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and choose **Create app**:
   - API: tick **Web API**
   - Redirect URI: `http://127.0.0.1:8888/callback` (must match exactly)
2. Create a `spotify` virtualenv with the dependencies, using [virtualenvwrapper](https://virtualenvwrapper.readthedocs.io/). `-a .` links it to this folder, so `workon spotify` also changes into it.
   ```bash
   mkvirtualenv -a . -r requirements.txt spotify   # requests, pyyaml, anthropic, openai, mcp, pytest
   workon spotify                                  # in each new shell
   ```
3. Put your app's Client ID in a `.env` file. See [Setting up `.env`](#setting-up-env) below.

The first command that talks to Spotify opens your browser so you can approve access. The token is cached in `~/.config/spotify-playlist/token.json`, outside the repo, so later runs don't need the browser. Pass `--login` to sign in again.

### Setting up `.env`

Your Spotify settings go in a `.env` file in the repo folder, next to `playlist_generator.py`. The file is git-ignored, so it's never committed.

1. Find your **Client ID** on your app's page in the [developer dashboard](https://developer.spotify.com/dashboard), under **Settings**.
2. Create `.env` and make it readable only by you:
   ```bash
   cat > .env <<'ENV'
   # Local Spotify settings: git-ignored, do not commit.

   # playlist_generator.py (PKCE sign-in, no secret needed)
   SPOTIFY_CLIENT_ID=<your client id>

   # spotify_playbook.ipynb also needs the app secret (Settings → View client secret):
   # SPOTIFY_CLIENTSECRET=<your client secret>

   # Optional: AI summary in `info`. Add the key for each provider you want to use.
   # ANTHROPIC_API_KEY=<your Anthropic API key>   # https://platform.claude.com
   # OPENAI_API_KEY=<your OpenAI API key>         # https://platform.openai.com
   ENV
   chmod 600 .env
   ```
3. Check that it's picked up and ignored:
   ```bash
   python playlist_generator.py list   # signs in on first run
   git check-ignore .env                # prints ".env" if it's ignored
   ```

How it's read:

- `playlist_generator.py` loads `.env` from its own folder automatically, so it works from any directory.
- A variable already set in your shell takes priority over `.env`. This lets you try another app with `SPOTIFY_CLIENT_ID=... python playlist_generator.py list`.
- Format: one `KEY=value` per line. Lines starting with `#` are comments; quotes and a leading `export ` are allowed.
- `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are only used by `info` for its AI summary, and you only need the one for the provider you choose. Without them, `info` still prints the stats and tracklist.
- The Client ID isn't a password, but the Client Secret and the API keys are. Keep `.env` out of git, screenshots and chats.

### Commands

```bash
# Preview what would be added (no sign-in, nothing changes)
python playlist_generator.py create party_2026.yaml --dry-run

# Create the playlist
python playlist_generator.py create party_2026.yaml

# Update an existing playlist with the same name in place (keeps its link)
python playlist_generator.py create party_2026.yaml --replace

# Check the playlist on Spotify matches the file; prints it by section with lengths
python playlist_generator.py check party_2026.yaml

# Summary (length, tracks, artists, years), AI-written overview and full tracklist
python playlist_generator.py info "Party 2026"                             # overview by Claude Opus 5.5 (default)
python playlist_generator.py info "Party 2026" --model claude              # same, named explicitly
python playlist_generator.py info "Party 2026" --model openai              # overview by gpt-5.5
python playlist_generator.py info "Party 2026" --model claude-sonnet-5-5   # any full model ID
python playlist_generator.py info "Party 2026" --no-ai                     # skip the overview

# Write a short AI description to the playlist on Spotify (shows it and asks first)
python playlist_generator.py info "Party 2026" --no-ai --set-description --file party_2026.yaml

# List your playlists (--all includes ones you follow, --filter TEXT, --json)
python playlist_generator.py list

# Remove a playlist by name, ID or link (asks first unless --yes)
python playlist_generator.py delete "Party 2026"
```

| Command | What it does |
|---|---|
| `create FILE` | Builds a playlist in file order. Stops with an error if you already own a playlist with that name, unless you pass `--replace` (swap its tracks, keep its link) or `--allow-duplicate-name`. Every track is looked up before anything changes, and at the end it lists any it couldn't add. Other options: `--name`, `--description`, `--public`. |
| `check FILE` | Compares the Spotify playlist with the file track by track, using exact track IDs. Prints the tracklist by section with each section's track count and running time, and marks mismatches with ✗. Exits with code 1 on a mismatch. Finds the playlist by the file's `name`; pass `--name` if it's called something else on Spotify. |
| `info TARGET` | Shows a playlist's link, owner, visibility, description, track and artist counts, total and average length, shortest and longest tracks, top artists and album years by decade, a short AI-written overview (mood, arc, standout moments), then the full tracklist with lengths and years. `TARGET` is a name, ID or link. Album years come from the album on Spotify, so remasters and compilations can show a later year. Choose who writes the overview with `--model` (see [AI summary models](#ai-summary-models)); `--no-ai` skips it. `--set-description` writes a short AI description to the playlist on Spotify (see [Setting the Spotify description](#setting-the-spotify-description)). |
| `list` | Shows your playlists with track counts, visibility and IDs. |
| `delete TARGET` | Removes a playlist from your library. Spotify has no hard delete; you can recover a removed playlist from your account page (*Recover playlists*). Use `--all-matches` when several playlists share a name. |

Global options go before the command:

| Option | What it does |
|---|---|
| `-V`, `--version` | Prints the version, release date and author, e.g. `playlist_generator.py 1.2.1 (2026-11-09) by Mal Minhas`. |
| `--login` | Ignores the cached Spotify token and signs in again, e.g. `python playlist_generator.py --login list`. |
| `-h`, `--help` | Lists the commands; `python playlist_generator.py COMMAND --help` lists a command's options. |

### AI summary models

`info` can ask Claude (Anthropic) or an OpenAI model to write its overview. Pick one with `--model`:

| `--model` | Provider | Model used | Key needed in `.env` |
|---|---|---|---|
| *(not given)*, `claude` or `anthropic` | Anthropic | Claude Opus 5.5 (`claude-opus-5-5`) | `ANTHROPIC_API_KEY` |
| `openai` or `gpt` | OpenAI | `gpt-5.5` | `OPENAI_API_KEY` |
| `claude-…` | Anthropic | the model you name, e.g. `claude-sonnet-5-5`, `claude-haiku-5-5` | `ANTHROPIC_API_KEY` |
| `gpt-…`, `chatgpt-…`, `o` + a digit | OpenAI | the model you name, e.g. `gpt-6-sol`, `o3` | `OPENAI_API_KEY` |

```bash
python playlist_generator.py info "Party 2026"                              # Claude Opus 5.5
python playlist_generator.py info "Party 2026" --model openai               # gpt-5.5
python playlist_generator.py info "Party 2026" --model claude-sonnet-5-5    # cheaper Claude
python playlist_generator.py info "Party 2026" --model gpt-6-sol            # another OpenAI model
```

How it works:

- **The model name picks the provider:** anything starting `claude-` goes to Anthropic; `gpt-`, `chatgpt-` or `o` followed by a digit goes to OpenAI. Other names are rejected with a short message. Shortcuts aren't case-sensitive.
- **Same prompt for both:** the playlist's name, description and full tracklist (artist, title, length, album year), with a request for two or three short paragraphs on genres and eras, the mood and energy arc, standout tracks or transitions, and the setting it suits.
- **APIs used:** Claude through the Anthropic Messages API at `medium` effort; OpenAI through the Responses API. On Claude Fable 5.1, Opus 5.5, Opus 5 and Sonnet 5.5, Anthropic's server-side fallback is on: if the model declines the request, Anthropic re-runs it on its recommended fallback model.
- **Failures don't break the overview:** a missing or rejected key, an unknown model, a rate limit, a network problem or a declined request prints `AI summary: skipped (…)` with the reason, and the stats and tracklist still appear. `--set-description` is stricter, because you asked for a change: if the AI step fails, it stops with `Description not changed: …` and exit code 1, and Spotify is left as it was.
- **Cost and privacy:** the overview is one billed API call, typically a few cents, to the provider you chose; `--set-description` adds a second (use `--no-ai` with it to make just one). The playlist's name, description and tracklist are sent to that provider; nothing else from your Spotify account is.

To see which OpenAI models your key can use:

```bash
python -c "from openai import OpenAI; print('\n'.join(sorted(m.id for m in OpenAI().models.list())))"
```

Run `set -a; source .env; set +a` first so the key is in your environment. For Claude, the current models are listed at https://platform.claude.com/docs/en/about-claude/models/overview.

### Setting the Spotify description

`info --set-description` asks the chosen model (the same `--model` choices as the overview) for a one- or two-sentence description and writes it to the playlist on Spotify:

```bash
python playlist_generator.py info "Party 2026" --no-ai --set-description                          # Claude
python playlist_generator.py info "Party 2026" --no-ai --set-description --model openai           # gpt-5.5
python playlist_generator.py info "Party 2026" --no-ai --set-description --file party_2026.yaml   # also save it
```

1. It shows the proposed description, its length, and the description it would replace.
2. It asks `Set this as the playlist description? [y/N]`. Anything but `y` leaves Spotify unchanged; `--yes` skips the question.
3. It updates Spotify, then reads the description back and confirms Spotify stored exactly that text.

**Spotify's rules.** Spotify doesn't officially document a limit, and community reports say clients differ (300 characters in some apps, up to 512 in others). So every description is cleaned to fit the strictest version:

- at most **300 characters**: the model is asked for 250, and anything longer is trimmed at a sentence end, or else at a word with `…`
- a **single line of plain text**: line breaks, tabs and control characters become spaces, and HTML tags, `<`/`>`, Markdown (`*`, `_`, `#`, backticks) and surrounding quotes are removed
- **never empty**: the API ignores an empty description, so an empty result is rejected
- only on **playlists you own**

**Keep it with `--file`.** `create --replace` writes the file's `description` back to Spotify. Pass `--file party_2026.yaml` (or `.json`) to save the new description there too, so the next replace keeps it. YAML comments and layout are preserved.

`--no-ai` only skips the long overview, so `--no-ai --set-description` makes a single AI call.

### Tracklist format

YAML is shown below. JSON with the same keys works too.

```yaml
name: Party 2026                 # playlist name (or pass --name)
description: Psych to ambient    # optional
public: false                    # optional, default false
tracks:
  - title: Cherry-Coloured Funk
    artist: Cocteau Twins
    section: Arrival             # groups tracks in `check` output
    style: Dream pop             # free text, for your reference
    length: "3:12"               # free text, for your reference
    notes: anything you like
    link: https://open.spotify.com/track/37pKTyMwalomKCZjxTc2QZ

  - title: Some song with no link yet
    artist: Some Artist          # no link → looked up with Spotify search

  - title: My mix
    link: https://open.spotify.com/playlist/4afTuHYJ0R3j0zU4Kpd7tb
                                 # playlist link → all its tracks (only playlists you own)

  - title: Chukhung
    link: https://open.spotify.com/album/5QIf4hNIAksV1uMCXHVkAZ
    track_numbers: [3]           # album link → its tracks, optionally filtered
    # discs: [1]                 # by disc and/or by track number

  - title: Maybe later
    artist: Someone
    skip: true                   # kept in the file, left out of the playlist
```

Only `link` (or `title` and `artist` when there's no link) decides what gets added. Links can be `open.spotify.com` URLs or `spotify:track:` / `album:` / `playlist:` URIs.

### Tracklists stay local

`*.yaml`, `*.yml` and `*.json` files are git-ignored, so your tracklists and playlist backups stay on your machine. To share one, add it with `git add -f FILE`.

### Tests

The tests are in `tests/` and use [pytest](https://docs.pytest.org/). They check everything without touching Spotify, Anthropic or OpenAI: a fake Spotify client and fake AI replies stand in for the real services, and each test builds its own sample tracklists in a temporary folder. So they run in a fresh clone, need no `.env` or sign-in, and cost nothing.

| File | Tests | What it checks |
|---|---|---|
| `test_playlist_generator.py` | 49 | The CLI script: every command and helper |
| `test_mcp_server.py` | 26 | The MCP server's tools, called directly |
| `test_mcp_stdio.py` | 1 | The MCP server end to end over the real stdio protocol, as Claude connects to it (it starts `fake_spotify_server.py` as the server process) |

```bash
workon spotify                      # the virtualenv from Setup
pip install -r requirements.txt     # includes pytest (only needed once)
pytest                              # run all tests from the repo root
pytest -v                           # list each test and its result
pytest -k description               # only tests whose name contains "description"
python -m pytest                    # same as pytest, if pytest isn't on your PATH
```

A passing run ends with `76 passed`. A failure names the test and shows what differed; fix it before committing.

What they cover:

- loading YAML and JSON tracklists, reading Spotify links, and `.env` settings (the shell wins over the file)
- `list` (owned, `--all`, `--filter`, `--json`), `delete` (asking first, by name, ID or link, shared names, `--all-matches`), and `create --dry-run` (no sign-in)
- `check` and `info` output: section totals, mismatch lines and exit code, stats and tracklist
- `create --replace`: updating in place, batches of 100 in file order, skipped entries, refusing an existing name without `--replace`
- AI models: which provider each model name uses, the `claude`/`openai` shortcuts, and a missing key skipping the overview
- Spotify description rules: one plain-text line, HTML and Markdown removed, at most 300 characters
- `info --set-description`: declining, accepting, read-back checks, saving with `--file`, own playlists only, and AI failures
- `-V` output
- the MCP server: read-only and playback tools marked correctly, device choice, playing from a track number, rewinding a resumed track, pause/resume/skip/volume, each tool's results, path checks for tracklist files, Spotify errors becoming tool errors, nothing printed to stdout (which carries the MCP protocol), and a full session over stdio: start-up, 14 tools listed, tool calls, and errors returned without stopping the server

Run them before committing changes to `playlist_generator.py` or `mcp_server.py`.

---

## MCP server: playlists in Claude

`mcp_server.py` is a local [MCP](https://modelcontextprotocol.io/) server. Once it's added to Claude Code or Claude Desktop, you can ask Claude about your playlists in plain language, for example *"Does Party 2026 still match party_2026.yaml?"* or *"Find Underworld's Rez on Spotify and give me the link for my tracklist."*

**It never changes your playlists or library.** The read-only tools only read from Spotify or local tracklist files. The playback tools only control what's playing, and Claude asks before using them. To change a playlist, Claude gives you the `playlist_generator.py` command to run.

| Tool | What it returns |
|---|---|
| `list_playlists` | Your playlists (optionally filtered by name, or including followed ones) with track counts, visibility, IDs and links |
| `playlist_info` | One playlist's description, track and artist counts, total and average length, shortest and longest tracks, top artists, album years by decade, and the full tracklist with links |
| `read_tracklist` | A local YAML/JSON tracklist, including sections, notes and skipped entries |
| `check_playlist` | Whether a playlist matches a tracklist file, with section totals and every position that differs |
| `search_tracks` | Up to 10 Spotify search results with links to paste into a tracklist |
| `list_devices` | Spotify apps that can play: Mac or phone app, web player, speakers |
| `now_playing` | The current track, position, device, volume and playlist |

Playback tools (need Spotify Premium and an open Spotify app):

| Tool | What it does |
|---|---|
| `play_playlist` | Plays a playlist, optionally from a track number, on a chosen device and volume |
| `play_track` | Plays one track, by link or by search (e.g. "Rez Underworld") |
| `pause`, `resume`, `next_track`, `previous_track`, `set_volume` | Control what's playing |

Try *"Play Party 2026 from track 25 at 30% volume"* or *"What's playing?"*. A web player only shows up as a device after you've pressed play in it once. If Spotify resumes a track part-way through, `play_playlist` rewinds it to the start.

The AI overview isn't a tool: Claude writes it from `playlist_info`, so the server needs no Anthropic or OpenAI key.

### Setup

1. Complete the [generator setup](#setup-once), including `.env`, and sign in once with the CLI so the token is cached:
   ```bash
   workon spotify
   python playlist_generator.py list
   ```
2. Add the server, using the full paths to the `spotify` virtualenv's Python and to `mcp_server.py`:

   **Claude Code**, for all your projects:
   ```bash
   claude mcp add -s user spotify-playlists -- ~/.virtualenvs/spotify/bin/python "$PWD/mcp_server.py"
   ```
   Then run `claude mcp list` to check it's connected, or `/mcp` inside Claude Code.

   **Claude Desktop**: see [Claude Desktop configuration](#claude-desktop-configuration) below.

### Claude Desktop configuration

Claude Desktop starts local MCP servers listed in its config file:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

1. **Quit Claude Desktop** (⌘Q). It only reads the file when it starts.
2. **Back up the file** first, if it exists:
   ```bash
   cp ~/Library/Application\ Support/Claude/claude_desktop_config.json{,.bak}
   ```
3. **Add `spotify-playlists` under `mcpServers`.** Use full paths: `~` and `$HOME` aren't expanded here.
   ```json
   {
     "mcpServers": {
       "spotify-playlists": {
         "command": "/Users/<you>/.virtualenvs/spotify/bin/python",
         "args": ["/Users/<you>/path/to/spotify/mcp_server.py"]
       }
     }
   }
   ```
   - `command` is the `spotify` virtualenv's Python, so the server has its packages. `ls ~/.virtualenvs/spotify/bin/python` shows the path.
   - `args` is the full path to `mcp_server.py` in your clone. Run `pwd` in the repo folder to get it.
   - If the file already has other servers or settings, add `"spotify-playlists": {…}` as one more entry inside the existing `mcpServers`, with a comma after the entry before it. Leave everything else as it is.
4. **Check the file is still valid JSON.** A missing or extra comma makes Desktop ignore the whole file.
   ```bash
   python3 -m json.tool ~/Library/Application\ Support/Claude/claude_desktop_config.json > /dev/null && echo OK
   ```
5. **Approve Spotify access before opening Desktop**, so the first tool call doesn't wait on a browser sign-in:
   ```bash
   workon spotify
   python -c "import mcp_server; mcp_server.spotify()"   # opens the browser once; click Agree
   ```
6. **Start Claude Desktop** and open **Settings → Customize → Connectors**. `spotify-playlists` should be listed as **Desktop · Local dev** with a tick. In a chat, it's also in the tools menu under the message box.

**Keep secrets out of this file.** The config needs no keys, tokens or Client ID: the server reads `SPOTIFY_CLIENT_ID` (and any API keys) from the repo's git-ignored `.env`, and keeps its Spotify sign-in in `~/.config/spotify-playlist/token.json`. Don't add an `"env"` block with keys here; the file is plain text outside the repo, and it's easy to paste into a chat or screenshot.

**Using it in a chat.** If the built-in **Spotify** web connector is also turned on, Claude may pick that one, and it can't play music. Name the server, e.g. *"Using spotify-playlists, play Party 2026 from track 25 at 30% volume"*, or turn the built-in connector off for that chat. For playback, have a Spotify app open; a web player needs play pressed once first. Desktop asks before running each playback tool.

**If it doesn't appear or a tool fails**, Desktop logs the server's start-up and every call here:
```bash
tail -50 ~/Library/Logs/Claude/mcp-server-spotify-playlists.log
```
The usual causes are invalid JSON, a wrong path in `command` or `args`, or Desktop not restarted. If Spotify reports missing permissions, run step 5 again.

**How it works:**

- It runs on your machine over stdio and reuses the generator's Spotify sign-in, cached token and `.env`, wherever Claude starts it from.
- The playback tools need two extra Spotify permissions. Only the server asks for them, so the CLI is unchanged. The first time, your browser opens once to approve; run `python -c "import mcp_server; mcp_server.spotify()"` in the repo to do this before using Claude. Running the CLI's `--login` afterwards removes them again, until you next approve.
- Tracklist paths can be absolute or relative to the repo folder (e.g. `party_2026.yaml`). Only `.yaml`, `.yml` and `.json` files can be read.
- Problems such as an unknown playlist, a missing file or a Spotify error come back to Claude as a tool error with the reason.
- It only works on this computer. Using it from claude.ai on the web or phone would need a hosted server with its own sign-in.

---

## Jupyter notebook: Spotify playbook

`spotify_playbook.ipynb` (v0.2, July 2023) has two parts.

### 1. Exploring a playlist with `spotipy`

This part pulls down a playlist by URL or URI with [`spotipy`](https://spotipy.readthedocs.io/), using the client-credentials flow. It shows the playlist's link and cover art, loads the tracks into a `pandas` DataFrame, and plots track popularity.

It needs a Client ID and a Client Secret. Uncomment `SPOTIFY_CLIENTSECRET` in your [`.env`](#setting-up-env) and fill it in. Note there's no underscore before `SECRET`.

The notebook doesn't read `.env` by itself, so load it into your shell before starting Jupyter (see [Running it](#running-it)).

Change `playlist_id` in the first code cell to explore a different playlist.

> This part was written against the 2023 API. Spotify's February 2026 changes renamed playlist `tracks` to `items` and restricted what apps in Development Mode can read, so it may need updating. It hasn't been re-tested since.

### 2. Streaming history analysis

This part analyses your own listening, based on [this approach](https://github.com/jayeshironside/Spotify-Streaming-history-analysis):

1. Request your data from your [Spotify privacy page](https://www.spotify.com/uk/account/privacy/) (see `DataCopy.png`). Basic account data takes up to 5 days; extended streaming history takes up to 30.
2. Unzip it so the notebook can find `MyData/StreamingHistory0.json` (about the last year of plays).
3. Run the cells to see:
   - how varied your listening is (the share of unique artists)
   - your top artists by hours played and by play count
   - your most-played tracks
   - your average plays per day
   - a word cloud of your favourite artists

`MyData/` and the JSON files are git-ignored, so your listening history isn't committed.

### Running it

```bash
workon spotify
pip install jupyter spotipy pandas matplotlib wordcloud
set -a; source .env; set +a          # export the .env settings to Jupyter
jupyter notebook spotify_playbook.ipynb
```
