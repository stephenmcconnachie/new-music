#!/usr/bin/env python3
"""
anydecentmusic.com Album Scraper
=================================
Scrapes https://anydecentmusic.com/ for new album reviews with rating >= 7.
Appends new entries to a growing HTML file that can feed into the Apple Music
skill for playlist creation.

Output: ~/Documents/new-music.html (or custom path via NEW_MUSIC_HTML env or --output)
State: seen.json next to this script (tracks already-seen albums)

Usage:
    python3 scrape.py                       # normal run
    python3 scrape.py --output ~/path.html  # custom output
    python3 scrape.py --reset               # reset seen state
"""

import os, re, json, sys
import html as html_lib
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from html import escape as h

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "seen.json"
DEFAULT_OUTPUT = Path(os.path.expanduser("~/Documents/new-music.html"))


def fetch_page(url: str) -> str:
    """Fetch HTML content from URL."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_albums(html: str) -> list[dict]:
    """Extract album entries from the main chart HTML."""
    albums = []

    # Find each <li> inside the main chart <ol>
    lis = re.findall(
        r'<li[^>]*class="([^"]*)"[^>]*>.*?<div class="album_detail">(.*?)</div>\s*</li>',
        html,
        re.DOTALL
    )

    for li_class, detail_html in lis:
        # Extract rating from score_wrap
        score_m = re.search(r'<p\s+class="score">([\d.]+)</p>', detail_html)
        if not score_m:
            continue
        try:
            rating = float(score_m.group(1))
        except ValueError:
            continue

        # Extract artist from <h4><a>
        artist_m = re.search(
            r'<h4[^>]*>\s*<a[^>]*>(.*?)</a>\s*</h4>',
            detail_html
        )
        artist = html_lib.unescape(artist_m.group(1).strip()) if artist_m else ""

        # Extract album from <h5><a href="...">
        album_m = re.search(
            r'<h5[^>]*>\s*<a\s+href="([^"]*)"[^>]*>(.*?)</a>\s*</h5>',
            detail_html
        )
        album = html_lib.unescape(album_m.group(2).strip()) if album_m else ""
        album_url = html_lib.unescape(album_m.group(1).strip()) if album_m else ""
        # Normalize relative URLs
        if album_url and not album_url.startswith("http"):
            album_url = "https://anydecentmusic.com" + album_url

        if not artist or not album:
            continue

        # Extract description (first <p> after <h5>)
        desc_m = re.search(
            r'</h5>\s*<p[^>]*>(.*?)</p>',
            detail_html,
            re.DOTALL
        )
        description = html_lib.unescape(desc_m.group(1).strip()) if desc_m else ""

        # Extract date from <small>Added: DD/MM/YYYY</small>
        date_m = re.search(
            r'<small>Added:\s*([\d/]+)</small>',
            detail_html
        )
        date_added = html_lib.unescape(date_m.group(1).strip()) if date_m else ""

        # Determine if new this week
        is_new = "new" in li_class

        albums.append({
            "artist": artist,
            "album": album,
            "album_url": album_url,
            "rating": rating,
            "description": description,
            "date_added": date_added,
            "is_new": is_new,
        })

    return albums


def load_state() -> set:
    """Load already-seen album keys."""
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except (json.JSONDecodeError, ValueError):
            pass
    return set()


def save_state(seen: set):
    """Save seen album keys."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=2))


def album_key(a: dict) -> str:
    """Unique key for an album (artist ||| album)."""
    return f"{a['artist']} ||| {a['album']}"


def format_date() -> str:
    return datetime.now().strftime("%d %b %Y")


def append_html(output_path: Path, new_entries: list[dict]):
    """Append new entries to the growing HTML file."""
    today = format_date()

    # If no new entries and file already exists, just update the date line
    if not new_entries and output_path.exists():
        existing = output_path.read_text()
        existing = re.sub(
            r'Updated: .*? ·',
            f'Updated: {today} ·',
            existing
        )
        output_path.write_text(existing)
        return output_path

    # Build fresh document
    lines = []
    lines.append("<!DOCTYPE html>")
    lines.append('<html lang="en">')
    lines.append("<head>")
    lines.append('  <meta charset="UTF-8">')
    lines.append('  <meta name="viewport" content="width=device-width, initial-scale=1.0">')
    lines.append("  <title>New Music — Weekly Picks</title>")
    lines.append("  <style>")
    lines.append("    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; ")
    lines.append("           max-width: 900px; margin: 0 auto; padding: 20px; background: #111; color: #eee; }")
    lines.append("    table { width: 100%; border-collapse: collapse; }")
    lines.append("    th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #333; }")
    lines.append("    th { background: #222; color: #ffbb22; font-weight: 600; position: sticky; top: 0; }")
    lines.append("    tr:hover { background: #1a1a2e; }")
    lines.append("    .rating { font-weight: bold; color: #4ade80; }")
    lines.append("    .rating-7 { color: #86efac; }")
    lines.append("    .rating-8 { color: #4ade80; }")
    lines.append("    .rating-9 { color: #22d3ee; }")
    lines.append("    .rating-10 { color: #fbbf24; }")
    lines.append("    .date { color: #888; font-size: 0.85em; }")
    lines.append("    .summary { color: #ccc; font-size: 0.9em; }")
    lines.append("    h1 { color: #ffbb22; }")
    lines.append("    .count { color: #888; margin-bottom: 20px; }")
    lines.append("    .badge { display: inline-block; background: #ffbb22; color: #000; ")
    lines.append("             border-radius: 4px; padding: 2px 8px; font-size: 0.8em; font-weight: bold; }")
    lines.append("  </style>")
    lines.append("</head>")
    lines.append("<body>")
    lines.append(f'  <h1>🎵 New Music Picks</h1>')
    lines.append(f'  <p class="count">Albums with rating ≥ 7 from <a href="https://anydecentmusic.com/" style="color:#ffbb22">anydecentmusic.com</a></p>')

    # Collect existing entries if file exists
    all_entries = []
    if output_path.exists():
        existing_html = output_path.read_text()
        # Parse existing table rows (handle both linked and plain album names)
        rows = re.findall(
            r'<tr[^>]*>.*?<td>(.*?)</td>.*?<td>(.*?)</td>.*?<td class="rating[^"]*">([\d.]+)</td>.*?<td class="summary">(.*?)</td>.*?<td class="date">(.*?)</td>.*?</tr>',
            existing_html,
            re.DOTALL
        )
        for row in rows:
            artist_text = h(row[0].strip(), quote=False)
            album_cell = row[1].strip()
            # Extract album_url and album text from <a> tag if present
            url_m = re.search(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', album_cell)
            if url_m:
                album_text = h(url_m.group(2).strip(), quote=False)
                album_url = url_m.group(1)
            else:
                album_text = h(album_cell.replace("<em>", "").replace("</em>", "").strip(), quote=False)
                album_url = ""
            all_entries.append({
                "artist": artist_text,
                "album": album_text,
                "album_url": album_url,
                "rating": float(row[2]),
                "description": row[3].strip(),
                "date": row[4].strip(),
            })

    # Add new entries
    for entry in new_entries:
        all_entries.append({
            "artist": entry["artist"],
            "album": entry["album"],
            "album_url": entry.get("album_url", ""),
            "rating": entry["rating"],
            "description": entry.get("description", ""),
            "date": today,
        })

    # Sort by rating descending, then by date
    all_entries.sort(key=lambda x: (-x["rating"], x.get("date", "")))

    # Write table
    lines.append("  <table>")
    lines.append("    <thead><tr>")
    lines.append("      <th>Artist</th><th>Album</th><th>Rating</th><th>Summary</th><th>Added</th>")
    lines.append("    </tr></thead>")
    lines.append("    <tbody>")

    for entry in all_entries:
        rating_class = f"rating-{min(int(entry['rating']), 10)}"
        artist = h(entry["artist"], quote=False)
        album = h(entry["album"], quote=False)
        summary = h(entry.get("description", ""), quote=False)
        date = h(entry.get("date", ""), quote=False)
        # Link album title to anydecentmusic.com if URL available
        album_url = entry.get("album_url", "")
        if album_url:
            album_html = f'<a href="{h(album_url, quote=True)}" target="_blank" rel="noopener" style="color:#86efac;text-decoration:none;"><em>{album}</em></a>'
        else:
            album_html = f"<em>{album}</em>"
        lines.append(f'    <tr>')
        lines.append(f'      <td><strong>{artist}</strong></td>')
        lines.append(f'      <td>{album_html}</td>')
        lines.append(f'      <td class="rating {rating_class}">{entry["rating"]}/10</td>')
        lines.append(f'      <td class="summary">{summary[:120]}{"…" if len(summary) > 120 else ""}</td>')
        lines.append(f'      <td class="date">{date}</td>')
        lines.append(f'    </tr>')

    lines.append("    </tbody>")
    lines.append("  </table>")
    lines.append(f'  <p class="count">Updated: {today} · {len(all_entries)} total albums</p>')
    lines.append("</body>")
    lines.append("</html>")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    return output_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape anydecentmusic.com for new albums")
    parser.add_argument("--output", help="Output HTML file path")
    parser.add_argument("--reset", action="store_true", help="Reset seen state")
    parser.add_argument("--min-rating", type=int, default=7, help="Minimum rating (default: 7)")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else DEFAULT_OUTPUT

    if args.reset:
        save_state(set())
        print("✓ Seen state reset")
        return

    # Fetch page
    print("Fetching anydecentmusic.com...")
    try:
        html = fetch_page("https://anydecentmusic.com/")
    except Exception as e:
        print(f"✗ Failed to fetch: {e}")
        sys.exit(1)

    # Parse
    albums = parse_albums(html)
    print(f"  Found {len(albums)} total album entries")

    # Filter by rating
    high_rated = [a for a in albums if a["rating"] >= args.min_rating]
    print(f"  {len(high_rated)} with rating >= {args.min_rating}")

    if not high_rated:
        print("  No qualifying albums found this week")
        # Still update the file (to refresh the date)
        if output_path.exists():
            try:
                append_html(output_path, [])
                print(f"  Refreshed: {output_path}")
            except Exception as e:
                print(f"  Could not refresh: {e}")
        return

    # Check against seen state
    seen = load_state()
    new_entries = []
    for a in high_rated:
        key = album_key(a)
        if key not in seen:
            new_entries.append(a)
            seen.add(key)

    print(f"  {len(new_entries)} new since last check")

    if not new_entries:
        print("  No new albums — all already recorded")
        # Still update to refresh the file
        try:
            append_html(output_path, [])
            print(f"  Refreshed: {output_path}")
        except Exception as e:
            print(f"  Could not refresh: {e}")
        return

    # Save state
    save_state(seen)

    # Append to HTML
    result = append_html(output_path, new_entries)
    print(f"\n✓ Added {len(new_entries)} new entries")
    print(f"  Output: {result}")
    print(f"\nNew additions:")
    for a in new_entries:
        print(f"  {a['rating']}/10 — {a['artist']} — {a['album']}")


if __name__ == "__main__":
    main()
