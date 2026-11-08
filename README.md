# spotify

Various Spotify experiments:

- **[`playlist_generator.py`](playlist_generator.py)**: a command-line tool that builds Spotify playlists from YAML or JSON tracklists, then lists, checks and deletes them.
- **[`spotify_playbook.ipynb`](spotify_playbook.ipynb)**: a Jupyter notebook that explores a playlist with `spotipy` and analyses your Spotify streaming history.

---

## Playlist generator

`playlist_generator.py` takes a tracklist file (YAML or JSON) and creates a Spotify playlist with the tracks in file order. Tracklists are local files and are git-ignored (see [Tracklists stay local](#tracklists-stay-local)).

It targets the Spotify Web API as it stands after the February 2026 Development Mode changes (`POST /me/playlists`, `/playlists/{id}/items`, `DELETE /me/library`). Older libraries such as `spotipy` may fail on these endpoints. **The Spotify account that owns the developer app needs Premium.**

### Setup (once)

1. Go to the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and choose **Create app**:
   - API: tick **Web API**
   - Redirect URI: `http://127.0.0.1:8888/callback` (must match exactly)
2. Install the dependencies:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install requests pyyaml     # pyyaml is only needed for YAML input
   ```
3. Set your app's Client ID. No Client Secret is needed, because sign-in uses PKCE.
   ```bash
   export SPOTIFY_CLIENT_ID=<your client id>
   ```

The first command that talks to Spotify opens your browser so you can approve access. The token is cached in `~/.config/spotify-playlist/token.json`, outside the repo, so later runs don't need the browser. Pass `--login` to sign in again.

### Commands

```bash
# Preview what would be added (no sign-in, nothing changes)
.venv/bin/python playlist_generator.py create party_2026.yaml --dry-run

# Create the playlist
.venv/bin/python playlist_generator.py create party_2026.yaml

# Update an existing playlist with the same name in place (keeps its link)
.venv/bin/python playlist_generator.py create party_2026.yaml --replace

# Check the playlist on Spotify matches the file; prints it by section with lengths
.venv/bin/python playlist_generator.py check party_2026.yaml

# List your playlists (--all includes ones you follow, --filter TEXT, --json)
.venv/bin/python playlist_generator.py list

# Remove a playlist by name, ID or link (asks first unless --yes)
.venv/bin/python playlist_generator.py delete "Party 2026"
```

| Command | What it does |
|---|---|
| `create FILE` | Builds a playlist in file order. Stops with an error if you already own a playlist with that name, unless you pass `--replace` (swap its tracks, keep its link) or `--allow-duplicate-name`. Every track is looked up before anything changes, and at the end it lists any it couldn't add. Other options: `--name`, `--description`, `--public`. |
| `check FILE` | Compares the Spotify playlist with the file track by track, using exact track IDs. Prints the tracklist by section with each section's track count and running time, and marks mismatches with ✗. Exits with code 1 on a mismatch. |
| `list` | Shows your playlists with track counts, visibility and IDs. |
| `delete TARGET` | Removes a playlist from your library. Spotify has no hard delete; you can recover a removed playlist from your account page (*Recover playlists*). Use `--all-matches` when several playlists share a name. |

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

It needs a Client ID and Client Secret from the same kind of developer app:

```bash
export SPOTIFY_CLIENT_ID=<your client id>
export SPOTIFY_CLIENTSECRET=<your client secret>   # note: no underscore before SECRET
```

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
.venv/bin/pip install jupyter spotipy pandas matplotlib wordcloud
.venv/bin/jupyter notebook spotify_playbook.ipynb
```
