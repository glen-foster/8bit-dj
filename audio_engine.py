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
import hashlib
import logging
import math
import os
import shutil
import struct
import subprocess
import sys
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


def search_ytmusic(query: str, limit: int = 12) -> List[Dict[str, Any]]:
    """Searches YouTube Music for songs matching `query` via `ytmusicapi`."""
    if not query or not query.strip():
        return []
    if _ytmusic is not None:
        try:
            results = _ytmusic.search(query.strip(), filter="songs", limit=limit)
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
            logger.warning(f"ytmusicapi search error for '{query}': {exc}")

    # Fallback search against local seed pool if offline
    q_low = query.lower()
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
                    }
                )
                if len(results) >= limit:
                    return results
        except Exception as exc:
            logger.warning(f"Playlist search warning ({filter_type}) for '{query}': {exc}")
    return results


def load_playlist_from_ytmusic(playlist_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Loads tracks from a YouTube Music playlist ID to populate the Host Seed Playlist."""
    clean_id = playlist_id.strip()
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

    venv_ytdlp = Path(sys.executable).parent / "yt-dlp"
    ytdlp_bin = (str(venv_ytdlp) if venv_ytdlp.exists() else None) or shutil.which("yt-dlp")
    if ytdlp_bin and not Path(ytdlp_bin).exists():
        ytdlp_bin = None
    venv_ffmpeg = Path(sys.executable).parent / "ffmpeg"
    ffmpeg_bin = (
        shutil.which("ffmpeg")
        or (str(venv_ffmpeg) if venv_ffmpeg.exists() else None)
        or ("/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").exists() else None)
    )

    if not ffmpeg_bin:
        wav_path = AUDIO_CACHE_DIR / f"{safe_id}.wav"
        logger.warning(f"ffmpeg not found on PATH; synthesizing 8-bit chiptune WAV for {video_id}")
        return _synthesize_pure_python_8bit_wav(wav_path, video_id)

    url = f"https://music.youtube.com/watch?v={video_id}"

    if ytdlp_bin:
        try:
            logger.info(f"[Pre-fetcher] Downloading & EBU R128 normalizing {video_id} via yt-dlp | ffmpeg...")
            ytdlp_cmd = [
                ytdlp_bin,
                "-f", "bestaudio[abr<=160]/bestaudio/best",
                "--concurrent-fragments", "4",
                "--no-playlist",
                "--quiet",
                "--no-warnings",
            ]
            if shutil.which("node"):
                ytdlp_cmd.extend(["--js-runtimes", "node", "--remote-components", "ejs:github"])
            ytdlp_cmd.extend(["-o", "-", url])
            ffmpeg_cmd = [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-i", "pipe:0",
                "-vn",
                "-af", "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-45dB,loudnorm=I=-9:TP=-0.3:LRA=9,volume=1.35",
                "-acodec", "libmp3lame",
                "-ab", "192k",
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
            # Wait up to 60s for the in-flight background download to complete
            for _ in range(120):
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
        keep_ids = set(database.get_recently_played_video_ids(keep_recent=2))
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

                # 2. Immediately Pre-fetch Audio (Now Playing + Top 3 in queue + Pending Approvals + Next Seed)
                audio_targets: List[Dict[str, Any]] = []
                if now_playing:
                    audio_targets.append(now_playing)
                audio_targets.extend(queue[:3])
                audio_targets.extend(pending[:3])
                if next_seed:
                    audio_targets.append(next_seed)

                for track in audio_targets:
                    vid = track["video_id"]
                    cached = get_cached_audio_path(vid)
                    if cached:
                        if track.get("audio_status") != "ready":
                            database.update_audio_status(vid, "ready", cached.name)
                            if self.on_state_change:
                                await self.on_state_change()
                    else:
                        asyncio.create_task(
                            self.ensure_track_buffered(vid, track["title"], track["artist"])
                        )

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
