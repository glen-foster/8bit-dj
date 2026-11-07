"""
Core Audio Engine for the 8-Bit Jukebox MVP.
- Uses `ytmusicapi` for search & playlist metadata.
- Maintains a background pre-fetcher task in FastAPI that inspects the current #1 song
  in the queue (and the next seed song) and downloads audio via `yt-dlp`, piping it
  through `ffmpeg` with EBU R128 loudness normalization (`-af loudnorm=I=-14:TP=-1.5:LRA=11`)
  into `/tmp/audio_cache/`.
- Provides a synthesized 8-bit chiptune fallback via `ffmpeg` (`aevalsrc`) if `yt-dlp` cannot
  reach YouTube (e.g. offline mode or restricted network), guaranteeing uninterrupted playback.
- Cleans up old cached files after playback.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import urllib.request
import wave
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from dotenv import load_dotenv

import ai_scene_generator
import database

load_dotenv()
logger = logging.getLogger("jukebox.audio")

AUDIO_CACHE_DIR = Path(os.getenv("AUDIO_CACHE_DIR", "/tmp/audio_cache"))
AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

try:
    from ytmusicapi import YTMusic
    _ytmusic = YTMusic()
except Exception as exc:
    logger.warning(f"YTMusic initialization warning: {exc}")
    _ytmusic = None


def _parse_duration_seconds(duration_str: Optional[str], duration_seconds: Optional[int] = None) -> int:
    if duration_seconds and isinstance(duration_seconds, int) and duration_seconds > 0:
        return duration_seconds
    if not duration_str:
        return 210
    parts = str(duration_str).strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except Exception:
        pass
    return 210


# Curated public Spotify Playlists that work 100% unauthenticated via Spotify's embed endpoint
CURATED_SPOTIFY_PLAYLISTS: List[Dict[str, str]] = [
    {"playlist_id": "spotify:37i9dQZF1DX4UtSsGT1Sbe", "title": "All Out 80s", "author": "Spotify", "item_count": "100", "keywords": "80s eighties retro synth pop rock party classic"},
    {"playlist_id": "spotify:37i9dQZF1DXbTxeAdrVG2l", "title": "All Out 90s", "author": "Spotify", "item_count": "100", "keywords": "90s nineties grunge pop dance party throwback"},
    {"playlist_id": "spotify:37i9dQZF1DX4o1oenSJRJd", "title": "All Out 2000s", "author": "Spotify", "item_count": "100", "keywords": "2000s 00s y2k pop rock hip hop party throwback"},
    {"playlist_id": "spotify:37i9dQZF1DWTJ7xPn4vNaz", "title": "All Out 70s", "author": "Spotify", "item_count": "100", "keywords": "70s seventies disco funk classic rock soul"},
    {"playlist_id": "spotify:37i9dQZF1DWXRqgorJj26U", "title": "Rock Classics", "author": "Spotify", "item_count": "100", "keywords": "rock classics guitar anthems 70s 80s 90s"},
    {"playlist_id": "spotify:37i9dQZF1DXcBWIGoYBM5M", "title": "Today's Top Hits", "author": "Spotify", "item_count": "50", "keywords": "top hits pop charts current party 2025 2026"},
    {"playlist_id": "spotify:37i9dQZF1DX26DKvjp0s9M", "title": "Indie Party", "author": "Spotify", "item_count": "75", "keywords": "indie rock alternative party dance"},
    {"playlist_id": "spotify:37i9dQZF1DX186v583rmzp", "title": "I Love My '90s Hip-Hop", "author": "Spotify", "item_count": "100", "keywords": "90s hip hop rap classic old school party"},
    {"playlist_id": "spotify:37i9dQZF1DX0XUsuxWHRQd", "title": "RapCaviar", "author": "Spotify", "item_count": "50", "keywords": "hip hop rap trap hits party"},
    {"playlist_id": "spotify:37i9dQZF1DX1MUPbVKMgJE", "title": "Disco Forever", "author": "Spotify", "item_count": "100", "keywords": "disco funk boogie 70s dance party"},
    {"playlist_id": "spotify:37i9dQZF1DXaXB8fQg7xif", "title": "Dance Party", "author": "Spotify", "item_count": "100", "keywords": "dance edm house club electronic party"},
    {"playlist_id": "spotify:37i9dQZF1DX3oM43CtKnRV", "title": "00s Rock Anthems", "author": "Spotify", "item_count": "100", "keywords": "00s 2000s rock indie alternative punk"},
    {"playlist_id": "spotify:37i9dQZF1DXdLEN7aqioXM", "title": "Nightrider (Synthwave / OutRun)", "author": "Spotify", "item_count": "100", "keywords": "synthwave outrun retro 8bit arcade cyber electronic"},
    {"playlist_id": "spotify:37i9dQZF1DX9wC1KY45plY", "title": "Classic Road Trip Songs", "author": "Spotify", "item_count": "100", "keywords": "road trip singalong classics rock pop party"},
    {"playlist_id": "spotify:37i9dQZF1DWY4lFlS4Pnso", "title": "Hot Hits UK", "author": "Spotify", "item_count": "50", "keywords": "uk hits pop dance charts party"},
]


def _extract_spotify_id(raw: str, expected_type: str = "playlist") -> Optional[str]:
    """Extracts a 22-character Spotify ID from a URL, URI, or `spotify:` prefixed string."""
    s = raw.strip()
    if s.startswith("spotify:") and not s.startswith(f"spotify:{expected_type}:"):
        candidate = s.split(":", 1)[1].strip()
        if re.match(r"^[A-Za-z0-9]{22}$", candidate):
            return candidate
    m = re.search(rf"(?:open\.spotify\.com/{expected_type}/|spotify:{expected_type}:)([A-Za-z0-9]{{22}})", s)
    if m:
        return m.group(1)
    if re.match(r"^[A-Za-z0-9]{22}$", s):
        return s
    return None


def _fetch_spotify_embed_entity(entity_type: str, spotify_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetches track or playlist metadata directly from Spotify's public embed page
    (`https://open.spotify.com/embed/{entity_type}/{spotify_id}`) without requiring
    any Spotify account, login, or API key.
    """
    url = f"https://open.spotify.com/embed/{entity_type}/{spotify_id}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
        if not m:
            return None
        data = json.loads(m.group(1))
        return data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity")
    except Exception as exc:
        logger.warning(f"Spotify embed fetch failed for {entity_type}/{spotify_id}: {exc}")
        return None


def search_ytmusic(query: str, limit: int = 12) -> List[Dict[str, Any]]:
    """
    Searches YouTube Music for songs matching `query` via `ytmusicapi`.
    Also supports pasting a public Spotify Track URL/URI (`open.spotify.com/track/...`)
    without requiring a Spotify account.
    """
    if not query or not query.strip():
        return []
    q_clean = query.strip()

    # If the user pasted a Spotify Track URL or URI, resolve its title & artist via Spotify's public embed
    if "open.spotify.com/track/" in q_clean or q_clean.startswith("spotify:track:"):
        sp_track_id = _extract_spotify_id(q_clean, "track")
        if sp_track_id:
            entity = _fetch_spotify_embed_entity("track", sp_track_id)
            if entity:
                title = entity.get("title") or ""
                artists = ", ".join(a.get("name", "") for a in (entity.get("artists") or []) if a.get("name"))
                q_clean = f"{title} {artists}".strip() or q_clean

    if _ytmusic is not None:
        try:
            results = _ytmusic.search(q_clean, filter="songs", limit=limit)
            parsed: List[Dict[str, Any]] = []
            for item in results:
                vid = item.get("videoId")
                if not vid:
                    continue
                artists = item.get("artists") or []
                artist_name = ", ".join(a.get("name", "") for a in artists if a.get("name")) or "Unknown Artist"
                thumbs = item.get("thumbnails") or []
                thumb_url = thumbs[-1].get("url", "") if thumbs else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                duration = _parse_duration_seconds(item.get("duration"), item.get("duration_seconds"))
                parsed.append(
                    {
                        "video_id": vid,
                        "title": item.get("title", "Unknown Track"),
                        "artist": artist_name,
                        "duration": duration,
                        "thumbnail": thumb_url,
                    }
                )
            if parsed:
                return parsed
        except Exception as exc:
            logger.warning(f"ytmusicapi search error for '{q_clean}': {exc}")

    # Fallback search against local seed pool if offline
    q_low = q_clean.lower()
    pool = database.get_seed_pool()
    matches = [
        t for t in pool if q_low in t["title"].lower() or q_low in t["artist"].lower()
    ]
    return matches[:limit]


def search_playlists_ytmusic(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Searches YouTube Music for community/featured playlists matching `query`."""
    if not query or not query.strip() or _ytmusic is None:
        return []
    results: List[Dict[str, Any]] = []
    seen_ids = set()
    for filter_type in ("community_playlists", "featured_playlists"):
        try:
            raw = _ytmusic.search(query.strip(), filter=filter_type, limit=limit)
            for item in raw:
                browse_id = item.get("browseId") or ""
                if not browse_id or browse_id in seen_ids:
                    continue
                seen_ids.add(browse_id)
                # Strip leading 'VL' if present so get_playlist accepts either
                clean_id = browse_id[2:] if browse_id.startswith("VL") else browse_id
                thumbs = item.get("thumbnails") or []
                thumb_url = thumbs[-1].get("url", "") if thumbs else ""
                author = item.get("author")
                if isinstance(author, list):
                    author_str = ", ".join(a.get("name", "") for a in author if isinstance(a, dict))
                elif isinstance(author, str):
                    author_str = author
                else:
                    author_str = "YouTube Music"
                results.append(
                    {
                        "playlist_id": clean_id,
                        "title": item.get("title", "Untitled Playlist"),
                        "author": author_str or "YouTube Music",
                        "item_count": str(item.get("itemCount") or ""),
                        "thumbnail": thumb_url,
                        "provider": "ytmusic",
                    }
                )
                if len(results) >= limit:
                    return results
        except Exception as exc:
            logger.warning(f"Playlist search warning ({filter_type}) for '{query}': {exc}")
    return results


def search_playlists_spotify(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """
    Unauthenticated Spotify playlist search:
    1. If `query` is a Spotify Playlist URL/URI or 22-char ID, fetches its live metadata
       directly from `open.spotify.com/embed/playlist/{id}` (no Spotify account needed).
    2. Matches against curated official Spotify playlists (80s, 90s, Rock, Indie, Hip-Hop, Disco, etc.).
    """
    if not query or not query.strip():
        return [
            {
                "playlist_id": p["playlist_id"],
                "title": p["title"],
                "author": "Spotify Official",
                "item_count": p["item_count"],
                "thumbnail": "",
                "provider": "spotify",
            }
            for p in CURATED_SPOTIFY_PLAYLISTS[:limit]
        ]

    q_clean = query.strip()
    sp_id = _extract_spotify_id(q_clean, "playlist")
    if sp_id and ("spotify" in q_clean.lower() or len(q_clean) == 22):
        entity = _fetch_spotify_embed_entity("playlist", sp_id)
        if entity:
            track_list = entity.get("trackList") or []
            cover_sources = (entity.get("coverArt") or {}).get("sources") or []
            thumb = cover_sources[-1].get("url", "") if cover_sources else ""
            return [
                {
                    "playlist_id": f"spotify:{sp_id}",
                    "title": entity.get("name") or entity.get("title") or "Spotify Playlist",
                    "author": entity.get("subtitle") or "Spotify",
                    "item_count": str(len(track_list)),
                    "thumbnail": thumb,
                    "provider": "spotify",
                }
            ]

    tokens = [t for t in re.split(r"\s+", q_clean.lower()) if t]
    scored: List[tuple[int, Dict[str, str]]] = []
    for item in CURATED_SPOTIFY_PLAYLISTS:
        haystack = f"{item['title']} {item['keywords']}".lower()
        score = sum(3 if tok in item["title"].lower() else (1 if tok in haystack else 0) for tok in tokens)
        if score > 0 or q_clean.lower() in ("all", "spotify", "popular", "hits"):
            scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    results: List[Dict[str, Any]] = [
        {
            "playlist_id": p["playlist_id"],
            "title": p["title"],
            "author": "Spotify Official (No Account Needed)",
            "item_count": p["item_count"],
            "thumbnail": "",
            "provider": "spotify",
        }
        for _, p in scored[:limit]
    ]

    # If the user searched a niche theme not in the curated list, return all curated + YTMusic matches
    if not results:
        for p in CURATED_SPOTIFY_PLAYLISTS[:4]:
            results.append(
                {
                    "playlist_id": p["playlist_id"],
                    "title": p["title"],
                    "author": "Spotify Official (No Account Needed)",
                    "item_count": p["item_count"],
                    "thumbnail": "",
                    "provider": "spotify",
                }
            )
    return results[:limit]


def load_playlist_from_spotify(playlist_id_or_url: str, limit: int = 30) -> List[Dict[str, Any]]:
    """
    Loads tracks from any public Spotify playlist URL, URI, or ID without a Spotify account
    by scraping Spotify's public embed JSON (`open.spotify.com/embed/playlist/{id}`) and
    resolving each track in parallel to a playable YouTube Music `video_id`.
    """
    sp_id = _extract_spotify_id(playlist_id_or_url, "playlist")
    if not sp_id:
        raise ValueError(f"Invalid Spotify playlist URL or ID: {playlist_id_or_url}")

    entity = _fetch_spotify_embed_entity("playlist", sp_id)
    if not entity:
        raise RuntimeError(f"Could not fetch public Spotify playlist '{sp_id}'. Make sure the playlist is public.")

    raw_tracks = (entity.get("trackList") or [])[:limit]
    if not raw_tracks:
        raise RuntimeError("Spotify playlist returned 0 tracks.")

    if _ytmusic is None:
        raise RuntimeError("ytmusicapi is required to resolve Spotify tracks to playable audio streams.")

    tls = threading.local()

    def _get_thread_ytm() -> Any:
        if not hasattr(tls, "ytm"):
            from ytmusicapi import YTMusic
            tls.ytm = YTMusic()
        return tls.ytm

    def _resolve_single_spotify_track(sp_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        title = (sp_item.get("title") or "").strip()
        artist = (sp_item.get("subtitle") or "").replace("\u00a0", " ").strip()
        dur_ms = sp_item.get("duration") or 210000
        dur_sec = max(30, int(dur_ms / 1000)) if isinstance(dur_ms, (int, float)) else 210
        if not title:
            return None
        try:
            ytm_client = _get_thread_ytm()
            hits = ytm_client.search(f"{title} {artist}".strip(), filter="songs", limit=1)
            if hits and hits[0].get("videoId"):
                hit = hits[0]
                vid = hit["videoId"]
                thumbs = hit.get("thumbnails") or []
                thumb_url = thumbs[-1].get("url", "") if thumbs else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                return {
                    "video_id": vid,
                    "title": title,
                    "artist": artist or "Unknown Artist",
                    "duration": _parse_duration_seconds(hit.get("duration"), hit.get("duration_seconds")) or dur_sec,
                    "thumbnail": thumb_url,
                }
        except Exception as exc:
            logger.debug(f"Spotify->YTMusic resolve warning for '{title} - {artist}': {exc}")
        return None

    logger.info(f"[Spotify Loader] Resolving {len(raw_tracks)} tracks from Spotify playlist {sp_id}...")
    with ThreadPoolExecutor(max_workers=4) as pool:
        resolved = list(pool.map(_resolve_single_spotify_track, raw_tracks))

    tracks: List[Dict[str, Any]] = []
    seen_vids = set()
    for t in resolved:
        if t and t["video_id"] not in seen_vids:
            seen_vids.add(t["video_id"])
            tracks.append(t)
    logger.info(f"[Spotify Loader] Successfully resolved {len(tracks)}/{len(raw_tracks)} Spotify tracks.")
    return tracks


def load_playlist_from_ytmusic(playlist_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Loads tracks from a YouTube Music playlist ID/URL OR a public Spotify playlist ID/URL
    to populate the Host Seed Playlist.
    """
    clean_id = playlist_id.strip()

    # Automatically route Spotify playlist URLs, URIs, `spotify:` prefixes, or 22-char Spotify IDs
    if (
        "open.spotify.com/playlist/" in clean_id
        or clean_id.startswith("spotify:")
        or (re.match(r"^[A-Za-z0-9]{22}$", clean_id) and not clean_id.startswith(("PL", "RD", "OL", "VL")))
    ):
        return load_playlist_from_spotify(clean_id, limit=min(limit, 30))

    if "list=" in clean_id:
        clean_id = clean_id.split("list=")[-1].split("&")[0]
    if clean_id.startswith("VL") and len(clean_id) > 4:
        clean_id = clean_id[2:]

    if _ytmusic is None:
        raise RuntimeError("ytmusicapi is not available.")

    pl = _ytmusic.get_playlist(clean_id, limit=limit)
    raw_tracks = pl.get("tracks") or []
    tracks: List[Dict[str, Any]] = []
    for item in raw_tracks:
        vid = item.get("videoId")
        if not vid:
            continue
        artists = item.get("artists") or []
        artist_name = ", ".join(a.get("name", "") for a in artists if a.get("name")) or "Unknown Artist"
        thumbs = item.get("thumbnails") or []
        thumb_url = thumbs[-1].get("url", "") if thumbs else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        duration = _parse_duration_seconds(item.get("duration"), item.get("duration_seconds"))
        tracks.append(
            {
                "video_id": vid,
                "title": item.get("title", "Unknown Track"),
                "artist": artist_name,
                "duration": duration,
                "thumbnail": thumb_url,
            }
        )
    return tracks


def get_cached_audio_path(video_id: str) -> Optional[Path]:
    """Returns the path to the normalized audio file if already cached and non-empty."""
    safe_id = "".join(c for c in video_id if c.isalnum() or c in ("-", "_"))
    for ext in (".mp3", ".wav"):
        target = AUDIO_CACHE_DIR / f"{safe_id}{ext}"
        if target.exists() and target.stat().st_size > 4096:
            return target
    return None


def _synthesize_pure_python_8bit_wav(output_path: Path, video_id: str, duration_sec: int = 35) -> Path:
    """
    Pure-Python 8-bit chiptune synthesizer fallback (normalized to -14 LUFS equivalent)
    used only when `ffmpeg` is not installed on a dev machine.
    """
    digest = int(hashlib.md5(video_id.encode("utf-8")).hexdigest(), 16)
    base_freq = 220.0 + (digest % 160)
    scale = [1.0, 1.25, 1.5, 1.875, 2.0, 1.5, 1.25, 1.125]
    sample_rate = 22050
    total_samples = sample_rate * duration_sec
    tempo_hz = 4 + (digest % 4)

    buf = bytearray()
    for i in range(total_samples):
        t = i / sample_rate
        step = int(t * tempo_hz) % len(scale)
        freq = base_freq * scale[step]
        # Square wave lead + triangle sub-bass
        lead = 1.0 if math.sin(2.0 * math.pi * freq * t) >= 0 else -1.0
        bass = math.sin(2.0 * math.pi * (base_freq * 0.5) * t)
        val = int((0.22 * lead + 0.18 * bass) * 32767)
        buf.extend(struct.pack("<h", max(-32768, min(32767, val))))

    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(buf))
    return output_path


def _download_and_normalize_sync(video_id: str) -> Path:
    """
    Downloads audio via `yt-dlp` and pipes it through `ffmpeg` with EBU R128
    loudness normalization (`-af loudnorm=I=-14:TP=-1.5:LRA=11`) into `/tmp/audio_cache/`.
    If `yt-dlp` fails (e.g., offline test environment), generates a normalized 8-bit chiptune
    loop with `ffmpeg` using the exact same EBU R128 loudnorm filter (or pure-Python WAV if
    `ffmpeg` is missing on a laptop) so the dual-deck crossfader and audio analyser always play.
    """
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c for c in video_id if c.isalnum() or c in ("-", "_"))
    final_path = AUDIO_CACHE_DIR / f"{safe_id}.mp3"
    temp_path = AUDIO_CACHE_DIR / f"{safe_id}.tmp.mp3"

    existing = get_cached_audio_path(video_id)
    if existing:
        return existing

    try:
        import imageio_ffmpeg
        imageio_ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        imageio_ffmpeg_bin = None

    venv_ffmpeg = Path(sys.executable).parent / "ffmpeg"
    ffmpeg_bin = (
        shutil.which("ffmpeg")
        or (str(venv_ffmpeg) if venv_ffmpeg.exists() else None)
        or ("/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").exists() else None)
        or ("/usr/local/bin/ffmpeg" if Path("/usr/local/bin/ffmpeg").exists() else None)
        or imageio_ffmpeg_bin
    )

    if not ffmpeg_bin:
        wav_path = AUDIO_CACHE_DIR / f"{safe_id}.wav"
        logger.warning(f"ffmpeg not found on PATH; synthesizing 8-bit chiptune WAV for {video_id}")
        return _synthesize_pure_python_8bit_wav(wav_path, video_id)

    url = f"https://music.youtube.com/watch?v={video_id}"

    try:
        import yt_dlp as _yt_dlp_mod  # noqa: F401
        ytdlp_cmd_prefix = [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        venv_ytdlp = Path(sys.executable).parent / "yt-dlp"
        ytdlp_bin = (str(venv_ytdlp) if venv_ytdlp.exists() else None) or shutil.which("yt-dlp")
        ytdlp_cmd_prefix = [ytdlp_bin] if (ytdlp_bin and Path(ytdlp_bin).exists()) else []

    if ytdlp_cmd_prefix:
        try:
            logger.info(f"[Pre-fetcher] Downloading & EBU R128 normalizing {video_id} via yt-dlp | ffmpeg...")
            ytdlp_cmd = [
                *ytdlp_cmd_prefix,
                "-f", "bestaudio[abr<=160]/bestaudio/best",
                "--concurrent-fragments", "4",
                "--no-playlist",
                "--quiet",
                "--no-warnings",
            ]
            if shutil.which("node"):
                ytdlp_cmd.extend(["--js-runtimes", "node"])
            ytdlp_cmd.extend(["-o", "-", url])
            ffmpeg_cmd = [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-i", "pipe:0",
                "-vn",
                "-af", "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-45dB,loudnorm=I=-14:TP=-1.5:LRA=11:dual_mono=true",
                "-acodec", "libmp3lame",
                "-q:a", "4",
                "-ar", "44100",
                str(temp_path),
            ]

            p_ytdlp = subprocess.Popen(ytdlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            p_ffmpeg = subprocess.Popen(
                ffmpeg_cmd,
                stdin=p_ytdlp.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if p_ytdlp.stdout:
                p_ytdlp.stdout.close()

            _, ff_err = p_ffmpeg.communicate(timeout=150)
            p_ytdlp.wait(timeout=10)

            if p_ffmpeg.returncode == 0 and temp_path.exists() and temp_path.stat().st_size > 8192:
                temp_path.replace(final_path)
                logger.info(f"[Pre-fetcher] Cached normalized track: {final_path}")
                return final_path
            else:
                logger.warning(
                    f"[Pre-fetcher] yt-dlp|ffmpeg pipeline returned {p_ffmpeg.returncode} "
                    f"({ff_err.decode('utf-8', errors='ignore')[:180]}). Generating 8-bit synth fallback."
                )
        except Exception as exc:
            logger.warning(f"[Pre-fetcher] yt-dlp pipe exception for {video_id}: {exc}")

    # Offline / Fallback 8-bit chiptune synthesizer piped through EBU R128 loudnorm
    digest = int(hashlib.md5(video_id.encode("utf-8")).hexdigest(), 16)
    base_freq = 220 + (digest % 160)
    arp1 = base_freq * 1.25
    arp2 = base_freq * 1.5
    tempo = 4 + (digest % 4)

    expr = (
        f"0.25*sgn(sin(2*PI*t*({base_freq}+({arp1}-{base_freq})*mod(floor(t*{tempo}),2)+"
        f"({arp2}-{base_freq})*mod(floor(t*{tempo}/2),2))))"
        f"+0.2*sin(2*PI*55*t*mod(floor(t*2),2))"
    )
    synth_cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "lavfi",
        "-i", f"aevalsrc='{expr}':s=44100:d=35",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-acodec", "libmp3lame",
        "-ab", "160k",
        str(temp_path),
    ]
    subprocess.run(synth_cmd, check=True, timeout=30)
    temp_path.replace(final_path)
    logger.info(f"[Pre-fetcher] Cached normalized 8-bit synth audio for {video_id} -> {final_path}")
    return final_path


class AudioPrefetchEngine:
    """
    Background manager that continuously:
    1. Pre-fetches and EBU R128 normalizes the Now Playing track, the #1 song in the queue,
       and the designated next Seed song into `/tmp/audio_cache/`.
    2. Pre-generates AI 8-bit scenes for Now Playing, Top 3 queued tracks, and next Seed song.
    3. Cleans up old audio files from `/tmp/audio_cache/` after playback.
    """

    def __init__(self, on_state_change: Optional[Callable[[], Coroutine[Any, Any, None]]] = None):
        self.on_state_change = on_state_change
        self._running = False
        self._inflight_downloads: set[str] = set()
        self._download_sem = asyncio.Semaphore(1)
        self._task: Optional[asyncio.Task] = None
        self._wakeup_event = asyncio.Event()

    def trigger_Pass(self) -> None:
        self._wakeup_event.set()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        self._running = False
        self._wakeup_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def ensure_track_buffered(
        self,
        video_id: str,
        title: str = "",
        artist: str = "",
        wait_if_inflight: bool = False,
    ) -> Optional[Path]:
        existing = get_cached_audio_path(video_id)
        if existing:
            database.update_audio_status(video_id, "ready", existing.name)
            return existing

        if video_id in self._inflight_downloads:
            if not wait_if_inflight:
                return None
            # Wait up to 120s for the in-flight background download to complete
            for _ in range(240):
                await asyncio.sleep(0.5)
                ready_path = get_cached_audio_path(video_id)
                if ready_path:
                    return ready_path
                if video_id not in self._inflight_downloads:
                    break
            return get_cached_audio_path(video_id)

        self._inflight_downloads.add(video_id)
        database.update_audio_status(video_id, "downloading", "")
        if self.on_state_change:
            await self.on_state_change()

        try:
            async with self._download_sem:
                existing_after_wait = get_cached_audio_path(video_id)
                if existing_after_wait:
                    path = existing_after_wait
                else:
                    path = await asyncio.to_thread(_download_and_normalize_sync, video_id)
            database.update_audio_status(video_id, "ready", path.name)
            if self.on_state_change:
                await self.on_state_change()
            return path
        except Exception as exc:
            logger.error(f"Failed to pre-fetch audio for {video_id}: {exc}")
            database.update_audio_status(video_id, "error", "")
            return None
        finally:
            self._inflight_downloads.discard(video_id)

    def cleanup_old_cache_files(self) -> None:
        """Removes cached MP3/WAV files that are no longer in the active queue, playing, or next seed."""
        keep_ids = set(database.get_recently_played_video_ids(keep_recent=10))
        keep_stems = {
            "".join(c for c in vid if c.isalnum() or c in ("-", "_"))
            for vid in keep_ids
        }
        try:
            for file_path in list(AUDIO_CACHE_DIR.glob("*.mp3")) + list(AUDIO_CACHE_DIR.glob("*.wav")):
                if file_path.name.endswith(".tmp.mp3"):
                    continue
                if file_path.stem not in keep_stems:
                    logger.info(f"[Cache Cleanup] Removing old audio file: {file_path.name}")
                    file_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"Cache cleanup warning: {exc}")

    async def _generate_scene_bg(self, vid: str, title: str, artist: str) -> None:
        try:
            await ai_scene_generator.ensure_scene_for_track(vid, title, artist)
            if self.on_state_change:
                await self.on_state_change()
        except Exception as exc:
            logger.warning(f"Background scene task error for {vid}: {exc}")

    async def _worker_loop(self) -> None:
        logger.info("Audio Pre-fetcher & AI Scene background worker started.")
        while self._running:
            try:
                now_playing = database.get_now_playing()
                queue = database.get_active_queue()
                pending = database.get_pending_approvals()
                next_seed = database.get_designated_next_seed()

                # 1. Fire-and-forget AI Scene Generation in parallel (never blocks audio downloading!)
                scene_targets: List[Dict[str, Any]] = []
                if now_playing:
                    scene_targets.append(now_playing)
                scene_targets.extend(queue[:3])
                scene_targets.extend(pending[:2])
                if next_seed:
                    scene_targets.append(next_seed)

                for track in scene_targets:
                    vid = track["video_id"]
                    cached_scene = database.get_cached_scene(vid)
                    if not cached_scene or cached_scene.get("source") == "classic_fallback" or cached_scene.get("version") != 3:
                        asyncio.create_task(
                            self._generate_scene_bg(vid, track["title"], track["artist"])
                        )

                # 2. Prioritized Audio Pre-fetching (Now Playing FIRST, then Top 3 in queue, Pending, Next Seed)
                #    We process one uncached track at a time in priority order so background downloads
                #    never compete with Now Playing or #1 Up Next for CPU/network bandwidth on the Pi.
                audio_targets: List[Dict[str, Any]] = []
                if now_playing:
                    audio_targets.append(now_playing)
                audio_targets.extend(queue[:3])
                audio_targets.extend(pending[:2])
                if next_seed:
                    audio_targets.append(next_seed)

                # First, sync 'ready' status for any tracks already on disk
                state_changed = False
                uncached_targets: List[Dict[str, Any]] = []
                seen_vids: set[str] = set()
                for track in audio_targets:
                    vid = track["video_id"]
                    if vid in seen_vids:
                        continue
                    seen_vids.add(vid)
                    cached = get_cached_audio_path(vid)
                    if cached:
                        if track.get("audio_status") != "ready":
                            database.update_audio_status(vid, "ready", cached.name)
                            state_changed = True
                    else:
                        uncached_targets.append(track)

                if state_changed and self.on_state_change:
                    await self.on_state_change()

                # Buffer the highest-priority uncached track right now; then loop to re-check priority!
                if uncached_targets:
                    highest_priority = uncached_targets[0]
                    await self.ensure_track_buffered(
                        highest_priority["video_id"],
                        highest_priority.get("title", ""),
                        highest_priority.get("artist", ""),
                    )
                    # Immediately re-evaluate queue in case Now Playing or #1 changed while downloading
                    continue

                # 3. Clean up old audio files after playback
                self.cleanup_old_cache_files()

            except Exception as exc:
                logger.error(f"Error in pre-fetcher loop: {exc}")

            try:
                await asyncio.wait_for(self._wakeup_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            finally:
                self._wakeup_event.clear()
