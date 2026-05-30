# New Music — Weekly Album Picker

Scrapes [anydecentmusic.com](https://anydecentmusic.com/) for new album reviews (rating ≥ 7/10), enriches with Apple Music availability/audio badges, and adds the **first track only** of each album to your "New albums" playlist in Apple Music.

```
scrape.py  ──▶  new-music.html  ──▶  enrich_apple_music.py  ──▶  publish
                    │
                    └──▶  add_to_apple_music.py  ──▶  Music.app "New albums" playlist
```

## Requirements

- **Python 3.10+** (uses modern union syntax)
- **macOS** (only needed for `add_to_apple_music.py` — the other scripts are platform-agnostic)
- No pip packages needed — all standard library

## Quick Start

```bash
# 1. Scrape new album reviews
python3 scrape.py

# 2. Enrich with Apple Music badges (Dolby Atmos, Lossless, availability)
python3 enrich_apple_music.py

# 3. Add to Apple Music library + "New albums" playlist
python3 add_to_apple_music.py
```

Open `~/Documents/new-music.html` in a browser to see the growing table.

## Scripts

### `scrape.py` — Fetch and parse album reviews

Scrapes anydecentmusic.com, extracts albums with rating ≥ 7.0, deduplicates via `seen.json`, and appends to a dark-themed HTML table.

```bash
python3 scrape.py                              # default: ~/Documents/new-music.html
python3 scrape.py --output ~/path/to/file.html # custom output path
python3 scrape.py --reset                      # clear seen state (force refetch)
python3 scrape.py --min-rating 8               # only albums rated 8+
```

**Output:** HTML file with columns: Artist, Album (hyperlinked to review), Rating, Summary, Added date. Entries sorted by rating descending.

**State:** `seen.json` tracks already-processed albums. Delete it to force a full re-check.

### `enrich_apple_music.py` — Add Apple Music badges

Queries the iTunes Store API for each album, fetches the store page to extract audio quality metadata, and adds an "Apple Music" column to the HTML.

```bash
python3 enrich_apple_music.py                      # full run (fresh API calls)
python3 enrich_apple_music.py --cache-only          # use cached results only
python3 enrich_apple_music.py --flush-cache         # clear cache before run
```

**Badges:**
| Badge | Meaning |
|-------|---------|
| **Atmos** | Available in Dolby Atmos |
| **Lossless** | Lossless audio (ALAC up to 24-bit/48kHz) |
| **Hi-Res** | Hi-Res Lossless (24-bit/192kHz) |
| *None* | Available, no special audio features detected |
| **Not on AM** | Not found on Apple Music store |

**Caching:** Results are cached in `am_cache.json`. Use `--cache-only` to skip API calls (useful for iterative HTML tweaks). Delete the cache to force a re-check.

**Search strategy** (used by both enrich and add-to-music scripts):

The iTunes Store API is searched using a 4-strategy fallback chain to handle ambiguous queries:

1. **Combined search** — `"{artist} {album}"` in GB store
2. **Artist lookup** — find artist ID, list all their albums (fixes "Cola" returning Hyphenrys, "American Football LP4" returning LP3)
3. **US store fallback** — retry combined search in US store
4. **Album-only search** — search album title alone with higher limit

### `add_to_apple_music.py` — Add to Apple Music (macOS only)

Adds albums to your Apple Music library and populates the "New albums" playlist.

```bash
python3 add_to_apple_music.py                                    # default HTML path
python3 add_to_apple_music.py --html-path ~/Documents/music.html # custom path
```

**How it works:**

1. **Library search** — Checks if the album is already in your Music.app library via AppleScript (fast, case-insensitive)
2. **Store search** — If not in library, searches Apple Music store using the same 4-strategy search as the enrich script
3. **GUI add** — Opens the store page in Music.app and clicks "Add to Library" via System Events accessibility API
4. **Playlist add** — Duplicates the first track to the "New albums" playlist (sampler, not full album)

**Required permissions:**
- **Accessibility** permission for Terminal (to click "Add to Library" via System Events)
  - System Settings → Privacy & Security → Accessibility → add Terminal
  - This is a one-time setup. The script will still open the album page in Music.app without it; you'll just need to click + manually.

**Known limitations:**
- Some niche/independent albums may not be on Apple Music store — silently skipped
- The GUI click may not work if Music.app's window layout differs from expected — falls back to opening the store URL for manual addition
- AppleScript `whose` clause is case-insensitive but requires exact-ish name matching for compound filters

## Full Pipeline

```bash
# Do everything
python3 scrape.py && \
  python3 enrich_apple_music.py && \
  python3 add_to_apple_music.py

# With cache-only enrich (no fresh API calls)
python3 scrape.py && \
  python3 enrich_apple_music.py --cache-only && \
  python3 add_to_apple_music.py
```

## Automation (cron)

Add a weekly cron job to run Sundays:

```bash
# Every Sunday at 9am
0 9 * * 0 cd /path/to/new-music && python3 scrape.py && python3 enrich_apple_music.py --cache-only && python3 add_to_apple_music.py
```

Or use launchd for macOS-native scheduling — save this as `~/Library/LaunchAgents/com.user.new-music.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.new-music</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/new-music/scrape.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>0</integer>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

## File Layout

```
new-music/
├── README.md
├── .gitignore
├── scrape.py               # anydecentmusic.com scraper (pure Python)
├── enrich_apple_music.py   # Apple Music badge enricher (pure Python)
├── add_to_apple_music.py   # Apple Music playlist integration (macOS only)
├── seen.json               # dedup state (auto-created, gitignored)
└── am_cache.json           # badge cache (auto-created, gitignored)
```

## Configuration

The output HTML path defaults to `~/Documents/new-music.html`. Override via:

- `NEW_MUSIC_HTML` environment variable
- `--output` flag on `scrape.py`
- `--html-path` flag on `add_to_apple_music.py`
- Edit the `OUTPUT` constant in `enrich_apple_music.py`

## How the Search Works

The Apple Music search uses a confidence-scoring system to avoid false positives:

**Artist matching (max 40):**
- Exact match → 40 points
- Substring match → 30 points
- Zero word overlap → -50 penalty

**Album name matching (max 60):**
- Name is normalized (strips punctuation, collapses whitespace, removes " - Single"/" - EP" suffixes)
- Exact normalized match → 60 points
- Substring match → 50 points
- Word overlap ≥ 70% → 40 points

Results below threshold are rejected, preventing known false positives like "C.O.L.A." by Hyphenrys for "Cost Of Living Adjustment" by Cola.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `add_to_apple_music.py` says "GUI click failed" | Ensure Terminal has Accessibility permission. The album URL is still opened — click + manually in Music.app. |
| "Not on AM" for an album you know exists | Run `enrich_apple_music.py --flush-cache` to clear stale cache and re-check. |
| Albums missing from the HTML | The chart only shows ~6 weeks' worth. Run weekly to catch everything. |
| `scrape.py` finds nothing new | All current entries are already in `seen.json`. Delete it and re-run to force a full re-fetch. |
| Playlist has duplicate tracks | The script checks the playlist before adding. If duplicates appear, clear the "New albums" playlist and re-run. |
