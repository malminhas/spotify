# spotify

Various Spotify experiments:

- **[`playlist_generator.py`](playlist_generator.py)**: a command-line tool that builds Spotify playlists from YAML or JSON tracklists, then lists, checks, summarises (with an optional Claude- or OpenAI-written overview) and deletes them.
- **[`spotify_playbook.ipynb`](spotify_playbook.ipynb)**: a Jupyter notebook that explores a playlist with `spotipy` and analyses your Spotify streaming history.

---

## Playlist generator

`playlist_generator.py` takes a tracklist file (YAML or JSON) and creates a Spotify playlist with the tracks in file order. Tracklists are local files and are git-ignored (see [Tracklists stay local](#tracklists-stay-local)).

It targets the Spotify Web API as it stands after the February 2026 Development Mode changes (`POST /me/playlists`, `/playlists/{id}/items`, `DELETE /me/library`). Older libraries such as `spotipy` may fail on these endpoints. **The Spotify account that owns the developer app needs Premium.**

### Setup (once)

1. Go to the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and choose **Create app**:
   - API: tick **Web API**
   - Redirect URI: `http://127.0.0.1:8888/callback` (must match exactly)
2. Create a `spotify` virtualenv with the dependencies, using [virtualenvwrapper](https://virtualenvwrapper.readthedocs.io/). `-a .` links it to this folder, so `workon spotify` also changes into it.
   ```bash
   mkvirtualenv -a . -r requirements.txt spotify   # requests, pyyaml, anthropic, openai
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
   python playlist_generator.py list     # signs in on first run
   git check-ignore .env                            # prints ".env" if it's ignored
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
python playlist_generator.py info "Party 2026"                  # overview by Claude Opus 5.5
python playlist_generator.py info "Party 2026" --model openai   # overview by gpt-5.5
python playlist_generator.py info "Party 2026" --no-ai          # skip the overview

# List your playlists (--all includes ones you follow, --filter TEXT, --json)
python playlist_generator.py list

# Remove a playlist by name, ID or link (asks first unless --yes)
python playlist_generator.py delete "Party 2026"
```

| Command | What it does |
|---|---|
| `create FILE` | Builds a playlist in file order. Stops with an error if you already own a playlist with that name, unless you pass `--replace` (swap its tracks, keep its link) or `--allow-duplicate-name`. Every track is looked up before anything changes, and at the end it lists any it couldn't add. Other options: `--name`, `--description`, `--public`. |
| `check FILE` | Compares the Spotify playlist with the file track by track, using exact track IDs. Prints the tracklist by section with each section's track count and running time, and marks mismatches with ✗. Exits with code 1 on a mismatch. |
| `info TARGET` | Shows a playlist's link, owner, visibility, description, track and artist counts, total and average length, shortest and longest tracks, top artists and album years by decade, a short AI-written overview (mood, arc, standout moments), then the full tracklist with lengths and years. `TARGET` is a name, ID or link. Album years come from the album on Spotify, so remasters and compilations can show a later year. Choose who writes the overview with `--model` (see [AI summary models](#ai-summary-models)); `--no-ai` skips it. |
| `list` | Shows your playlists with track counts, visibility and IDs. |
| `delete TARGET` | Removes a playlist from your library. Spotify has no hard delete; you can recover a removed playlist from your account page (*Recover playlists*). Use `--all-matches` when several playlists share a name. |

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
- **Failures never break `info`:** a missing or rejected key, an unknown model, a rate limit, a network problem or a declined request prints `AI summary: skipped (…)` with the reason, and the stats and tracklist still appear.
- **Cost and privacy:** each run is one billed API call, typically a few cents, to the provider you chose. The tracklist is sent to that provider; nothing else from your Spotify account is.

To see which OpenAI models your key can use:

```bash
python -c "from openai import OpenAI; print('\n'.join(sorted(m.id for m in OpenAI().models.list())))"
```

Run `set -a; source .env; set +a` first so the key is in your environment. For Claude, the current models are listed at https://platform.claude.com/docs/en/about-claude/models/overview.

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
