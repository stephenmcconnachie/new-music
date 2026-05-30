#!/usr/bin/env python3
"""
Add albums from new-music.html to Apple Music library and 'New albums' playlist.

- Albums in local library → deduplicate, add first track to playlist
- Albums NOT in library → search iTunes Store API, use multi-strategy search
  to find the correct store page, open in Music.app, click "Add to Library",
  then add first track to playlist

Usage: python3 add_to_apple_music.py [--html-path ~/Documents/new-music.html]
"""
import subprocess, json, re, time, urllib.request, urllib.parse, sys, os, argparse
from html import unescape
from pathlib import Path


KNOWN_ABSENT = []
KNOWN_ABSENT_KEYS = set(KA.lower() for KA in KNOWN_ABSENT)


def _osa(script, timeout=30):
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def _jsa(script, timeout=30):
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def ensure_playlist():
    _jsa('''
    var m = Application("Music"), names = m.playlists.name(), found = false;
    for (var i = 0; i < names.length; i++) { if (names[i] === "New albums") { found = true; break; } }
    if (!found) { Application("Music").make({new:"playlist",withProperties:{name:"New albums"}}); }
    ''')


def is_in_library(artist, album):
    esc_a = artist.replace("\\", "\\\\").replace('"', '\\"')[:80]
    esc_al = album.replace("\\", "\\\\").replace('"', '\\"')[:80]
    out = _osa(f'''
    tell application "Music"
      set r to (every track of library playlist 1 whose artist contains "{esc_a}" and album contains "{esc_al}")
      if (count of r) > 0 then return "YES"
      return "NO"
    end tell
    ''')
    return out == "YES"


def add_to_library_via_gui(store_url):
    _osa(f'open location "{store_url}"')
    time.sleep(3)
    result = _osa('''
    tell application "System Events"
      tell process "Music"
        try
          set addBtn to first button of toolbar 1 of window 1 whose description is "Add to Library"
          click addBtn
          return "CLICKED"
        on error
          return "NO_BUTTON"
        end try
      end tell
    end tell
    ''')
    time.sleep(3)
    return "CLICKED" in result


# ── iTunes Store Search (Multi-Strategy) ──────────────────────────


def _fetch_json(url):
    """Fetch a URL and return parsed JSON, or None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except Exception as e:
        return None


def _search_albums(term, country="GB", limit=10):
    """Search iTunes Store for albums matching 'term'."""
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode({
        "term": term, "entity": "album", "limit": limit, "country": country,
    })
    data = _fetch_json(url)
    if not data:
        return []
    results = []
    for r in data.get("results", []):
        results.append({
            "name": r["collectionName"],
            "artist": r["artistName"],
            "url": r["collectionViewUrl"].replace("uo=4", "app=music"),
            "artistId": r.get("artistId"),
            "collectionId": r.get("collectionId"),
        })
    return results


def _search_artists(term, country="GB", limit=5):
    """Search iTunes Store for artists matching 'term'."""
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode({
        "term": term, "entity": "musicArtist", "limit": limit, "country": country,
    })
    data = _fetch_json(url)
    if not data:
        return []
    results = []
    for r in data.get("results", []):
        results.append({
            "artistName": r["artistName"],
            "artistId": r["artistId"],
        })
    return results


def _lookup_artist_albums(artist_id, country="GB"):
    """Look up all albums by a given artist ID."""
    url = f"https://itunes.apple.com/lookup?id={artist_id}&country={country}&entity=album"
    data = _fetch_json(url)
    if not data:
        return []
    results = []
    for r in data.get("results", []):
        if r.get("wrapperType") == "collection":
            results.append({
                "name": r["collectionName"],
                "artist": r["artistName"],
                "url": r["collectionViewUrl"].replace("uo=4", "app=music"),
                "artistId": r.get("artistId"),
                "collectionId": r.get("collectionId"),
            })
    return results


def _artist_matches(result_artist_name, target_artist):
    """Check if a search result artist matches our target artist."""
    ra = result_artist_name.lower().strip()
    ta = target_artist.lower().strip()
    # Exact match or contained match
    if ra == ta or ta in ra or ra in ta:
        return True
    # Check significant word overlap (for multi-word artist names)
    words_ta = set(ta.split())
    words_ra = set(ra.split())
    common = words_ta & words_ra
    # If artist name has 2+ words and most are shared
    if len(words_ta) >= 2 and len(common) >= len(words_ta) - 1:
        return True
    if len(words_ra) >= 2 and len(common) >= len(words_ra) - 1:
        return True
    return False


def _album_matches(result_name, result_artist, target_artist, target_album):
    """Score how well a result matches our target (0-100)."""
    score = 0
    ta = target_artist.lower().strip()
    tb = target_album.lower().strip()
    ra = result_artist.lower().strip()
    rb = result_name.lower().strip()

    # Artist match (max 40)
    if ta == ra:
        score += 40
    elif ta in ra or ra in ta:
        score += 30
    else:
        # Penalize if artists are completely different
        words_a = set(ta.split())
        words_ra = set(ra.split())
        if words_a and words_ra and len(words_a & words_ra) == 0:
            score -= 50  # Heavy penalty for unrelated artist

    # Album name match (max 60)
    # Normalize: remove punctuation, extra spaces, common suffixes
    import re as _re
    tb_norm = _re.sub(r'[\(\)\[\]\-–—:;&]', ' ', tb).strip()
    tb_norm = _re.sub(r'\s+', ' ', tb_norm)
    rb_norm = _re.sub(r'[\(\)\[\]\-–—:;&]', ' ', rb).strip()
    rb_norm = _re.sub(r'\s+', ' ', rb_norm)
    # Remove " - Single", " - EP" suffixes
    rb_norm = _re.sub(r'\s*[-–—]\s*(Single|EP|Deluxe Edition|Bonus Track Version|Remastered|Live)\s*$', '', rb_norm, flags=_re.I)

    if tb_norm == rb_norm:
        score += 60
    elif tb_norm in rb_norm or rb_norm in tb_norm:
        score += 50
    else:
        # Check word overlap
        words_tb = set(tb_norm.split())
        words_rb = set(rb_norm.split())
        if words_tb and words_rb:
            overlap = len(words_tb & words_rb)
            max_len = max(len(words_tb), len(words_rb))
            if overlap / max_len >= 0.7:
                score += 40
            elif overlap / max_len >= 0.5:
                score += 30

    return max(0, min(100, score))


def _find_best_result(artist, album, results, min_score=50):
    """Among results, find best match. Returns (score, result) or None."""
    scored = [(_album_matches(r["name"], r["artist"], artist, album), r) for r in results]
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] >= min_score:
        return scored[0]
    return None


def search_itunes_multi(artist, album):
    """
    Multi-strategy iTunes Store search.

    1. Combined artist+album search (GB store, limit=10)
    2. If no good match, find artist ID → lookup their albums
    3. If still no good match, try US store
    4. As last resort, search just the album name (US + GB)
    """
    tb_lower = album.lower().strip()

    # Strategy 1: Combined search (GB, relaxed threshold)
    results = _search_albums(f"{artist} {album}", limit=10, country="GB")
    best = _find_best_result(artist, album, results, min_score=50)
    if best:
        score, result = best
        # If it's a fuzzy match (not exact), check if artist lookup yields a better result
        if score < 90:
            alt = _search_by_artist(artist, album)
            if alt:
                return alt
        return result

    # Strategy 2: Find artist, lookup their albums
    alt = _search_by_artist(artist, album)
    if alt:
        return alt

    # Strategy 3: Combined search with US store
    results_us = _search_albums(f"{artist} {album}", limit=10, country="US")
    best_us = _find_best_result(artist, album, results_us, min_score=45)
    if best_us:
        best = best_us[1]
        best["url"] = best["url"].replace("/us/", "/gb/")
        return best

    # Strategy 4: Search just the album name (high limit)
    for country in ("GB", "US"):
        album_results = _search_albums(album, limit=25, country=country)
        best_album = _find_best_result(artist, album, album_results, min_score=45)
        if best_album:
            r = best_album[1]
            if country == "US":
                r["url"] = r["url"].replace("/us/", "/gb/")
            return r

    return None


def _search_by_artist(artist, album):
    """Find artist ID, lookup albums, return best match or None."""
    tb_lower = album.lower().strip()
    for country in ("GB", "US"):
        artist_results = _search_artists(artist, limit=5, country=country)
        for ar in artist_results:
            if _artist_matches(ar["artistName"], artist):
                albums = _lookup_artist_albums(ar["artistId"], country=country)
                if not albums:
                    continue
                scored = [(_album_matches(a["name"], a["artist"], artist, album), a) for a in albums]
                scored.sort(key=lambda x: x[0], reverse=True)
                # Return best match if good enough
                if scored and scored[0][0] >= 55:
                    r = scored[0][1]
                    if country == "US":
                        r["url"] = r["url"].replace("/us/", "/gb/")
                    return r
                # Also check substring match on album name
                for s, a in scored:
                    if tb_lower in a["name"].lower() and s >= 40:
                        if country == "US":
                            a["url"] = a["url"].replace("/us/", "/gb/")
                        return a
    return None


# ── Library & Playlist Ops ────────────────────────────────────────


def is_in_playlist(track_name, track_artist):
    """Check if a track is already in the 'New albums' playlist."""
    esc_t = track_name.replace("\\", "\\\\").replace('"', '\\"')[:80]
    esc_a = track_artist.replace("\\", "\\\\").replace('"', '\\"')[:80]
    out = _osa(f'''
    tell application "Music"
      set p to (every playlist whose name is "New albums")
      if (count of p) = 0 then return "NO"
      set r to (every track of item 1 of p whose name is "{esc_t}" and artist is "{esc_a}")
      if (count of r) > 0 then return "YES"
      return "NO"
    end tell
    ''')
    return out == "YES"


def add_first_track(artist, album):
    """Add first track of album to 'New albums' playlist (skips if already present).
    Retries if the album was just added to library (sync delay).
    """
    esc_a = artist.replace("\\", "\\\\").replace('"', '\\"')[:80]
    esc_al = album.replace("\\", "\\\\").replace('"', '\\"')[:80]

    # Try multiple album name variations for matching
    album_variants = [album]
    # If album has parenthetical suffix, try without it
    import re as _re
    base = _re.sub(r'\s*\(.*?\)\s*$', '', album).strip()
    if base and base != album:
        album_variants.append(base)
    # Also try LP → Lp normalization
    if '(LP' in album:
        alt = album.replace('(LP', '(Lp')
        if alt not in album_variants:
            album_variants.append(alt)

    for attempt in range(3):  # Retry up to 3 times (with delay for new additions)
        for esc_al_var in [v.replace("\\", "\\\\").replace('"', '\\"')[:80] for v in album_variants]:
            out = _osa(f'''
            tell application "Music"
              set r to (every track of library playlist 1 whose artist contains "{esc_a}" and album contains "{esc_al_var}")
              if (count of r) > 0 then
                set t to item 1 of r
                set tn to name of t
                set ta to artist of t
                set p to (every playlist whose name is "New albums")
                if (count of p) > 0 then
                  set existing to (every track of item 1 of p whose name is tn and artist is ta)
                  if (count of existing) > 0 then
                    return "DUP:" & tn
                  end if
                end if
                duplicate t to playlist "New albums"
                return "OK:" & tn
              end if
            end tell
            ''')
            if out.startswith("OK:") or out.startswith("DUP:"):
                return out
        if attempt < 2:
            time.sleep(4)  # Wait for Music.app to sync new additions
    # Last attempt: try just artist name, any album
    out = _osa(f'''
    tell application "Music"
      set r to (every track of library playlist 1 whose artist contains "{esc_a}")
      if (count of r) > 0 then
        repeat with t in r
          set album_name to album of t
          if album_name contains "{esc_al}" then
            set tn to name of t
            set ta to artist of t
            set p to (every playlist whose name is "New albums")
            if (count of p) > 0 then
              set existing to (every track of item 1 of p whose name is tn and artist is ta)
              if (count of existing) > 0 then return "DUP:" & tn
            end if
            duplicate t to playlist "New albums"
            return "OK:" & tn
          end if
        end repeat
        return "MISS (found artist but no matching album)"
      end if
      return "MISS"
    end tell
    ''')
    return out


# ── Main ───────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Add new music to Apple Music library and playlist")
    parser.add_argument("--html-path", default=os.path.expanduser("~/Documents/new-music.html"),
                        help="Path to new-music.html (default: ~/Documents/new-music.html)")
    args = parser.parse_args()
    html_path = args.html_path

    html = open(html_path).read()
    rows = re.findall(
        r'<tr[^>]*>.*?<td><strong>(.*?)</strong></td>.*?<td>(?:<a[^>]*>)?<em>(.*?)</em>(?:</a>)?</td>.*?'
        r'<td class="rating[^\"]*\">([\d.]+)/10</td>.*?</tr>',
        html, re.DOTALL
    )

    _osa('tell application "Music" to activate')
    ensure_playlist()

    stats = {"lib_added": 0, "pl_added": 0, "skipped": 0, "mismatch": 0}

    for artist_html, album_html, _ in rows:
        artist = unescape(artist_html.strip())
        album = unescape(album_html.strip())

        # Check against known absent key
        key = f"{artist} — {album}".lower()
        if key in KNOWN_ABSENT_KEYS:
            print(f"• {artist} — {album}")
            print(f"  → Skipped (known absent from Apple Music)")
            stats["skipped"] += 1
            continue

        print(f"• {artist} — {album}")

        if not is_in_library(artist, album):
            print(f"  → Not in library, searching store...")
            best = search_itunes_multi(artist, album)

            if not best:
                print(f"  → No matching album found on Apple Music store")
                stats["skipped"] += 1
                continue

            print(f"  → Found: '{best['name']}' by {best['artist']}")
            print(f"  → Opening: {best['url']}")

            ok = add_to_library_via_gui(best["url"])
            if ok:
                print(f"  ✓ Added to library")
                stats["lib_added"] += 1
                time.sleep(4)
            else:
                print(f"  ✗ Could not auto-add (GUI click failed)")
                print(f"    URL: {best['url']}")
                print(f"    Open this in Music.app and click + to add")
                continue

        pl = add_first_track(artist, album)
        if pl.startswith("OK:"):
            print(f"  ✓ + {pl[3:]}")
            stats["pl_added"] += 1
        elif pl.startswith("DUP:"):
            print(f"  → Already in playlist: {pl[4:]}")
        else:
            print(f"  ? {pl}")

    print(f"\nDone: {stats['pl_added']} to playlist, "
          f"{stats['lib_added']} newly added to library, "
          f"{stats['skipped']} skipped, "
          f"{stats['mismatch']} wrong matches")


if __name__ == "__main__":
    main()
