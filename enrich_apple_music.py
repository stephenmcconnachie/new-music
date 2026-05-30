#!/usr/bin/env python3
"""
Enrich new-music.html with Apple Music availability + audio badges
(Dolby Atmos, Lossless, Hi-Res Lossless).

Reads the HTML, queries the iTunes Store API (multi-strategy search)
for each album, fetches the store page to extract audioBadges JSON,
then rewrites the HTML with an extra "Apple Music" column.

Usage:
    python3 enrich_apple_music.py
    python3 enrich_apple_music.py --cache-only   # skip fresh API calls
    python3 enrich_apple_music.py --flush-cache  # clear cache before run
"""
import os, sys, re, json, time, urllib.request, urllib.parse
from html import escape as h
from html import unescape
from pathlib import Path
from datetime import datetime

OUTPUT = Path(os.path.expanduser("~/Documents/new-music.html"))
CACHE_FILE = Path(__file__).resolve().parent / "am_cache.json"

KNOWN_ABSENT = []


# ── Multi-strategy iTunes Search (mirrors add_to_apple_music.py) ─────


def _fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        })
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception:
        return None


def _search_albums(term, country="GB", limit=10):
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
    ra = result_artist_name.lower().strip()
    ta = target_artist.lower().strip()
    if ra == ta or ta in ra or ra in ta:
        return True
    words_ta = set(ta.split())
    words_ra = set(ra.split())
    common = words_ta & words_ra
    if len(words_ta) >= 2 and len(common) >= len(words_ta) - 1:
        return True
    if len(words_ra) >= 2 and len(common) >= len(words_ra) - 1:
        return True
    return False


def _album_matches(result_name, result_artist, target_artist, target_album):
    """Score 0-100 how well a result matches our target."""
    score = 0
    ta = target_artist.lower().strip()
    tb = target_album.lower().strip()
    ra = result_artist.lower().strip()
    rb = result_name.lower().strip()

    # Artist component (max 40)
    if ta == ra:
        score += 40
    elif ta in ra or ra in ta:
        score += 30
    else:
        words_a = set(ta.split())
        words_ra = set(ra.split())
        if words_a and words_ra and len(words_a & words_ra) == 0:
            score -= 50

    # Album name component (max 60)
    import re as _re
    tb_norm = _re.sub(r'[\(\)\[\]\-–—:;&]', ' ', tb).strip()
    tb_norm = _re.sub(r'\s+', ' ', tb_norm)
    rb_norm = _re.sub(r'[\(\)\[\]\-–—:;&]', ' ', rb).strip()
    rb_norm = _re.sub(r'\s+', ' ', rb_norm)
    rb_norm = _re.sub(r'\s*[-–—]\s*(Single|EP|Deluxe Edition|Bonus Track Version|Remastered|Live)\s*$', '', rb_norm, flags=_re.I)

    if tb_norm == rb_norm:
        score += 60
    elif tb_norm in rb_norm or rb_norm in tb_norm:
        score += 50
    else:
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
    scored = [(_album_matches(r["name"], r["artist"], artist, album), r) for r in results]
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] >= min_score:
        return scored[0]
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
                if scored and scored[0][0] >= 55:
                    r = scored[0][1]
                    if country == "US":
                        r["url"] = r["url"].replace("/us/", "/gb/")
                    return r
                for s, a in scored:
                    if tb_lower in a["name"].lower() and s >= 40:
                        if country == "US":
                            a["url"] = a["url"].replace("/us/", "/gb/")
                        return a
    return None


def search_itunes_multi(artist, album):
    """Multi-strategy search: combined → artist lookup → US → album-only."""
    tb_lower = album.lower().strip()

    # Strategy 1: Combined search (GB, relaxed threshold)
    results = _search_albums(f"{artist} {album}", limit=10, country="GB")
    best = _find_best_result(artist, album, results, min_score=50)
    if best:
        score, result = best
        if score < 90:
            alt = _search_by_artist(artist, album)
            if alt:
                return alt
        return result

    # Strategy 2: Artist lookup
    alt = _search_by_artist(artist, album)
    if alt:
        return alt

    # Strategy 3: US store combined
    results_us = _search_albums(f"{artist} {album}", limit=10, country="US")
    best_us = _find_best_result(artist, album, results_us, min_score=45)
    if best_us:
        best = best_us[1]
        best["url"] = best["url"].replace("/us/", "/gb/")
        return best

    # Strategy 4: Album-only search
    for country in ("GB", "US"):
        album_results = _search_albums(album, limit=25, country=country)
        best_album = _find_best_result(artist, album, album_results, min_score=45)
        if best_album:
            r = best_album[1]
            if country == "US":
                r["url"] = r["url"].replace("/us/", "/gb/")
            return r
    return None


# ── Audio badge extraction ───────────────────────────────────────────


def extract_audio_badges(html):
    m = re.search(r'"audioBadges"\s*:\s*(\{[^}]+\})', html)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    return None


# ── Main ─────────────────────────────────────────────────────────────


def main():
    cache_only = "--cache-only" in sys.argv
    flush_cache = "--flush-cache" in sys.argv

    cache = {}
    if CACHE_FILE.exists() and not flush_cache:
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            cache = {}

    html = OUTPUT.read_text()

    # Parse rows
    rows = re.findall(
        r'<tr[^>]*>.*?<td><strong>(.*?)</strong></td>.*?<td>'
        r'(?:<a[^>]*href="([^"]*)"[^>]*>)?<em>(.*?)</em>(?:</a>)?</td>.*?'
        r'<td class="rating[^"]*">([\d.]+)/10</td>.*?'
        r'<td class="summary">(.*?)</td>.*?'
        r'<td class="date">(.*?)</td>.*?</tr>',
        html, re.DOTALL
    )

    if not rows:
        print("No rows found in HTML")
        return

    print(f"Checking {len(rows)} albums for Apple Music info...")

    enriched = []

    for row in rows:
        artist_raw = unescape(row[0].strip())
        album_url = row[1].strip() if len(row) > 1 and row[1] else ""
        album_raw = unescape(row[2].strip() if len(row) > 2 else row[1].strip())
        rating = row[3] if len(row) > 3 else row[2]
        summary = row[4].strip() if len(row) > 4 else row[3].strip()
        date = row[5].strip() if len(row) > 5 else row[4].strip()

        cache_key = f"{artist_raw} ||| {album_raw}"

        # Known absent
        if any(k.lower() in artist_raw.lower() for k in KNOWN_ABSENT):
            enriched.append((artist_raw, album_raw, album_url, rating, summary, date, False, None, ""))
            print(f"  - {artist_raw} — {album_raw}: known absent")
            continue

        # Cached
        if cache_key in cache:
            c = cache[cache_key]
            enriched.append((artist_raw, album_raw, album_url, rating, summary, date,
                           c.get("available", False), c.get("badges"), c.get("url", "")))
            status = "✓" if c.get("available") else "✗"
            badge_str = ""
            if c.get("badges"):
                if c["badges"].get("dolbyAtmos"): badge_str += " Atmos"
                if c["badges"].get("lossless"): badge_str += " Lossless"
                if c["badges"].get("hiResLossless"): badge_str += " Hi-Res"
            print(f"  {status} {artist_raw} — {album_raw} (cached){badge_str}")
            continue

        if cache_only:
            enriched.append((artist_raw, album_raw, album_url, rating, summary, date, False, None, ""))
            print(f"  ? {artist_raw} — {album_raw}: skipped (cache-only)")
            continue

        # Search iTunes with multi-strategy
        best = search_itunes_multi(artist_raw, album_raw)
        if not best:
            cache[cache_key] = {"available": False}
            enriched.append((artist_raw, album_raw, album_url, rating, summary, date, False, None, ""))
            print(f"  ✗ {artist_raw} — {album_raw}: no match found")
            continue

        # Fetch store page for audio badges
        store_html = _fetch_json.__code__  # not used; fetch_page is separate
        store_html = None
        try:
            req = urllib.request.Request(best["url"], headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                store_html = resp.read().decode("utf-8", errors="replace")
        except Exception:
            pass

        badges = None
        available = True
        if store_html:
            badges = extract_audio_badges(store_html)
        else:
            time.sleep(0.5)

        am_url = best["url"]
        cache[cache_key] = {"available": available, "badges": badges, "url": am_url}
        enriched.append((artist_raw, album_raw, album_url, rating, summary, date, available, badges, am_url))

        badge_str = ""
        if badges:
            if badges.get("dolbyAtmos"): badge_str += " Atmos"
            if badges.get("lossless"): badge_str += " Lossless"
            if badges.get("hiResLossless"): badge_str += " Hi-Res"
        print(f"  ✓ {artist_raw} — {album_raw}{badge_str}")

        time.sleep(1)

    # Save cache
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))

    # ── Rebuild HTML ─────────────────────────────────────────────────
    today = datetime.now().strftime("%d %b %Y")
    lines = []
    lines.append("<!DOCTYPE html>")
    lines.append('<html lang="en">')
    lines.append("<head>")
    lines.append('  <meta charset="UTF-8">')
    lines.append('  <meta name="viewport" content="width=device-width, initial-scale=1.0">')
    lines.append("  <title>New Music — Weekly Picks</title>")
    lines.append("  <style>")
    lines.append("    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; ")
    lines.append("           max-width: 1000px; margin: 0 auto; padding: 20px; background: #111; color: #eee; }")
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
    lines.append("    .am-atmos { color: #8b5cf6; font-weight: bold; font-size: 0.85em; }")
    lines.append("    .am-lossless { color: #f59e0b; font-weight: bold; font-size: 0.85em; }")
    lines.append("    .am-missing { color: #666; font-size: 0.85em; }")
    lines.append("    .am-available { color: #888; font-size: 0.85em; }")
    lines.append("    h1 { color: #ffbb22; }")
    lines.append("    .count { color: #888; margin-bottom: 20px; }")
    lines.append("  </style>")
    lines.append("</head>")
    lines.append("<body>")
    lines.append('  <h1>🎵 New Music Picks</h1>')
    lines.append('  <p class="count">Albums with rating ≥ 7 from <a target="_blank" rel="noopener" href="https://anydecentmusic.com/" style="color:#ffbb22">anydecentmusic.com</a></p>')
    lines.append("  <table>")
    lines.append("    <thead><tr>")
    lines.append("      <th>Artist</th><th>Album</th><th>Rating</th><th>Summary</th><th>Apple Music</th><th>Added</th>")
    lines.append("    </tr></thead>")
    lines.append("    <tbody>")

    for entry in enriched:
        artist, album, album_url, rating, summary, date, available, badges, am_url = entry
        rating_class = f"rating-{min(int(float(rating)), 10)}"
        artist_esc = h(artist, quote=False)
        album_esc = h(album, quote=False)
        summary_esc = h(summary, quote=False)
        date_esc = h(date, quote=False)

        # Album hyperlink to anydecentmusic.com
        if album_url:
            album_html = f'<a href="{h(album_url, quote=True)}" target="_blank" rel="noopener" style="color:#86efac;text-decoration:none;"><em>{album_esc}</em></a>'
        else:
            album_html = f"<em>{album_esc}</em>"

        # Apple Music badges
        if not available:
            am_html = '<span class="am-missing">Not on AM</span>'
        elif badges:
            parts = []
            if badges.get("dolbyAtmos"):
                parts.append('<span class="am-atmos">Atmos</span>')
            if badges.get("lossless"):
                parts.append('<span class="am-lossless">Lossless</span>')
            if badges.get("hiResLossless"):
                parts.append('<span class="am-lossless">Hi-Res</span>')
            badge_text = " · ".join(parts) if parts else '<span class="am-available">Available</span>'
            if am_url:
                am_html = f'<a href="{h(am_url, quote=True)}" target="_blank" rel="noopener" style="text-decoration:none;">{badge_text}</a>'
            else:
                am_html = badge_text
        else:
            am_html = '<span class="am-available">Available</span>'

        lines.append("    <tr>")
        lines.append(f'      <td><strong>{artist_esc}</strong></td>')
        lines.append(f'      <td>{album_html}</td>')
        lines.append(f'      <td class="rating {rating_class}">{rating}/10</td>')
        lines.append(f'      <td class="summary">{summary_esc[:120]}{"…" if len(summary_esc) > 120 else ""}</td>')
        lines.append(f'      <td>{am_html}</td>')
        lines.append(f'      <td class="date">{date_esc}</td>')
        lines.append("    </tr>")

    lines.append("    </tbody>")
    lines.append("  </table>")
    lines.append(f'  <p class=\"count\">Updated: {today} · {len(enriched)} total albums</p>')
    lines.append("</body>")
    lines.append("</html>")

    OUTPUT.write_text("\n".join(lines))

    num_avail = sum(1 for e in enriched if e[6])
    num_missing = sum(1 for e in enriched if not e[6])
    atmos = sum(1 for e in enriched if e[6] and e[7] and e[7].get("dolbyAtmos"))
    lossless = sum(1 for e in enriched if e[6] and e[7] and e[7].get("lossless"))
    hires = sum(1 for e in enriched if e[6] and e[7] and e[7].get("hiResLossless"))

    print(f"\n✓ Enriched: {OUTPUT}")
    print(f"  {num_avail} available, {num_missing} not available")
    if atmos: print(f"  {atmos} in Dolby Atmos")
    if lossless: print(f"  {lossless} Lossless")
    if hires: print(f"  {hires} Hi-Res Lossless")


if __name__ == "__main__":
    main()
