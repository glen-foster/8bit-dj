"""
SQLite Database Layer for the 8-Bit Jukebox MVP.
Persists queue, votes, DJ leaderboard, seed pool, and cached AI 8-bit scenes.
"""

import json
import random
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).parent / "jukebox.db"

# Family-friendly 8-bit username generator pools
RETRO_ADJECTIVES = [
    "Pixel", "Turbo", "Neon", "Cyber", "Chiptune", "Mega", "Super", "Hyper",
    "Glitch", "Arcade", "Atomic", "Cosmic", "Laser", "Retro", "Bit", "Synth",
    "Vortex", "Star", "Chrono", "Quant", "Warp", "Mecha", "8Bit", "256"
]

RETRO_NOUNS = [
    "Panda", "Falcon", "Ninja", "Wizard", "Knight", "Drifter", "Raccoon",
    "Phoenix", "Robot", "Samurai", "Koala", "Valkyrie", "Yoshi", "Ranger",
    "Golem", "Fox", "Badger", "Dolphin", "Tiger", "Dragon", "Sphinx", "Pilot"
]

# Pre-populated retro/synthwave/party starter seed pool so Fast Track works out-of-the-box
DEFAULT_SEED_POOL = [
    {
        "video_id": "fNFzfwLM72c",
        "title": "Stayin' Alive",
        "artist": "Bee Gees",
        "duration": 249,
        "thumbnail": "https://i.ytimg.com/vi/fNFzfwLM72c/hqdefault.jpg",
    },
    {
        "video_id": "4NRXx6U8ABQ",
        "title": "Blinding Lights",
        "artist": "The Weeknd",
        "duration": 200,
        "thumbnail": "https://i.ytimg.com/vi/4NRXx6U8ABQ/hqdefault.jpg",
    },
    {
        "video_id": "djV11Xbc914",
        "title": "Take On Me",
        "artist": "a-ha",
        "duration": 225,
        "thumbnail": "https://i.ytimg.com/vi/djV11Xbc914/hqdefault.jpg",
    },
    {
        "video_id": "dQw4w9WgXcQ",
        "title": "Never Gonna Give You Up",
        "artist": "Rick Astley",
        "duration": 213,
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    },
    {
        "video_id": "FTQbiNvZqaY",
        "title": "Africa",
        "artist": "Toto",
        "duration": 295,
        "thumbnail": "https://i.ytimg.com/vi/FTQbiNvZqaY/hqdefault.jpg",
    },
    {
        "video_id": "y6120QOlsfU",
        "title": "Sandstorm",
        "artist": "Darude",
        "duration": 232,
        "thumbnail": "https://i.ytimg.com/vi/y6120QOlsfU/hqdefault.jpg",
    },
    {
        "video_id": "FGBhQbmPwH8",
        "title": "One More Time",
        "artist": "Daft Punk",
        "duration": 320,
        "thumbnail": "https://i.ytimg.com/vi/FGBhQbmPwH8/hqdefault.jpg",
    },
    {
        "video_id": "Zi_XLOBDo_Y",
        "title": "Billie Jean",
        "artist": "Michael Jackson",
        "duration": 294,
        "thumbnail": "https://i.ytimg.com/vi/Zi_XLOBDo_Y/hqdefault.jpg",
    },
]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def generate_retro_username() -> str:
    adj = random.choice(RETRO_ADJECTIVES)
    noun = random.choice(RETRO_NOUNS)
    suffix = random.randint(10, 99)
    return f"{adj}_{noun}_{suffix}"


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                client_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                wins INTEGER DEFAULT 0,
                requests_count INTEGER DEFAULT 0,
                votes_cast INTEGER DEFAULT 0,
                updated_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                duration INTEGER DEFAULT 210,
                thumbnail TEXT DEFAULT '',
                requested_by_id TEXT NOT NULL,
                requested_by_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                votes INTEGER DEFAULT 1,
                tie_breaker REAL NOT NULL,
                audio_status TEXT DEFAULT 'pending',
                audio_file TEXT DEFAULT '',
                is_seed INTEGER DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s','now')),
                played_at REAL
            );

            CREATE TABLE IF NOT EXISTS votes (
                queue_id INTEGER NOT NULL,
                client_id TEXT NOT NULL,
                created_at REAL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (queue_id, client_id),
                FOREIGN KEY (queue_id) REFERENCES queue(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ai_scenes (
                video_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                scene_json TEXT NOT NULL,
                created_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS seed_tracks (
                video_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                duration INTEGER DEFAULT 210,
                thumbnail TEXT DEFAULT '',
                playlist_id TEXT DEFAULT 'default',
                added_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        # Recover from abrupt reboot: if more than one track is 'playing', keep only the latest
        playing_rows = conn.execute(
            "SELECT id FROM queue WHERE status = 'playing' ORDER BY played_at DESC, id DESC"
        ).fetchall()
        if len(playing_rows) > 1:
            for r in playing_rows[1:]:
                conn.execute("UPDATE queue SET status = 'played' WHERE id = ?", (r["id"],))

        # Populate default seed pool if empty
        count = conn.execute("SELECT COUNT(*) as c FROM seed_tracks").fetchone()["c"]
        if count == 0:
            for item in DEFAULT_SEED_POOL:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO seed_tracks (video_id, title, artist, duration, thumbnail, playlist_id)
                    VALUES (?, ?, ?, ?, ?, 'default_8bit_pool')
                    """,
                    (
                        item["video_id"],
                        item["title"],
                        item["artist"],
                        item["duration"],
                        item["thumbnail"],
                    ),
                )
        conn.commit()


def ensure_user(client_id: str, preferred_name: Optional[str] = None) -> Dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE client_id = ?", (client_id,)).fetchone()
        if row:
            return dict(row)
        username = preferred_name if preferred_name else generate_retro_username()
        conn.execute(
            "INSERT INTO users (client_id, username, wins, requests_count, votes_cast) VALUES (?, ?, 0, 0, 0)",
            (client_id, username),
        )
        conn.commit()
        return {
            "client_id": client_id,
            "username": username,
            "wins": 0,
            "requests_count": 0,
            "votes_cast": 0,
        }


def reroll_username(client_id: str) -> Dict[str, Any]:
    new_name = generate_retro_username()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE client_id = ?", (client_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (client_id, username, wins, requests_count, votes_cast) VALUES (?, ?, 0, 0, 0)",
                (client_id, new_name),
            )
        else:
            conn.execute(
                "UPDATE users SET username = ?, updated_at = ? WHERE client_id = ?",
                (new_name, time.time(), client_id),
            )
            # Also update active queue items requested by this user
            conn.execute(
                "UPDATE queue SET requested_by_name = ? WHERE requested_by_id = ? AND status IN ('queued', 'pending_approval')",
                (new_name, client_id),
            )
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE client_id = ?", (client_id,)).fetchone())


def add_to_queue(
    video_id: str,
    title: str,
    artist: str,
    duration: int,
    thumbnail: str,
    client_id: str,
    status: str = "queued",
    is_seed: bool = False,
) -> Dict[str, Any]:
    user = ensure_user(client_id, "8BIT_AUTODJ" if is_seed else None)
    tie_breaker = random.random()

    with get_conn() as conn:
        # Check if track is already in active queue or pending approval
        existing = conn.execute(
            "SELECT * FROM queue WHERE video_id = ? AND status IN ('queued', 'pending_approval', 'playing')",
            (video_id,),
        ).fetchone()
        if existing:
            return {"already_queued": True, "item": dict(existing)}

        cur = conn.execute(
            """
            INSERT INTO queue (
                video_id, title, artist, duration, thumbnail,
                requested_by_id, requested_by_name, status, votes, tie_breaker, is_seed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                title,
                artist,
                duration or 210,
                thumbnail or "",
                client_id,
                "8BIT_AUTODJ" if is_seed else user["username"],
                status,
                0 if is_seed else 1,
                tie_breaker,
                1 if is_seed else 0,
            ),
        )
        queue_id = cur.lastrowid

        if not is_seed and status == "queued":
            conn.execute(
                "INSERT OR IGNORE INTO votes (queue_id, client_id) VALUES (?, ?)",
                (queue_id, client_id),
            )
            conn.execute(
                "UPDATE users SET requests_count = requests_count + 1, votes_cast = votes_cast + 1 WHERE client_id = ?",
                (client_id,),
            )

        conn.commit()
        row = conn.execute("SELECT * FROM queue WHERE id = ?", (queue_id,)).fetchone()
        return {"already_queued": False, "item": dict(row)}


def cast_vote(queue_id: int, client_id: str) -> Dict[str, Any]:
    ensure_user(client_id)
    with get_conn() as conn:
        item = conn.execute("SELECT * FROM queue WHERE id = ? AND status = 'queued'", (queue_id,)).fetchone()
        if not item:
            return {"ok": False, "reason": "Track is no longer in the active queue."}

        existing = conn.execute(
            "SELECT 1 FROM votes WHERE queue_id = ? AND client_id = ?",
            (queue_id, client_id),
        ).fetchone()
        if existing:
            return {"ok": False, "reason": "ALREADY VOTED! 1 VOTE PER TRACK."}

        conn.execute(
            "INSERT INTO votes (queue_id, client_id) VALUES (?, ?)",
            (queue_id, client_id),
        )
        conn.execute(
            "UPDATE queue SET votes = votes + 1 WHERE id = ?",
            (queue_id,),
        )
        conn.execute(
            "UPDATE users SET votes_cast = votes_cast + 1 WHERE client_id = ?",
            (client_id,),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM queue WHERE id = ?", (queue_id,)).fetchone()
        return {"ok": True, "item": dict(updated)}


def get_client_voted_ids(client_id: str) -> List[int]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT v.queue_id FROM votes v
            JOIN queue q ON q.id = v.queue_id
            WHERE v.client_id = ? AND q.status = 'queued'
            """,
            (client_id,),
        ).fetchall()
        return [r["queue_id"] for r in rows]


def ensure_minimum_queue_size(min_count: int = 5) -> None:
    """
    Ensures the active queue (`status = 'queued'`) always contains at least `min_count`
    tracks by topping it up from `seed_tracks` (`is_seed = 1`, `votes = 0`).
    """
    ensure_user("system_seed", "8BIT_AUTODJ")
    with get_conn() as conn:
        current_rows = conn.execute(
            "SELECT video_id FROM queue WHERE status = 'queued'"
        ).fetchall()
        current_count = len(current_rows)
        if current_count >= min_count:
            return

        needed = min_count - current_count
        active_or_playing = {
            r["video_id"]
            for r in conn.execute(
                "SELECT video_id FROM queue WHERE status IN ('queued', 'playing', 'pending_approval')"
            ).fetchall()
        }
        recent_played = {
            r["video_id"]
            for r in conn.execute(
                "SELECT video_id FROM queue WHERE status = 'played' ORDER BY played_at DESC LIMIT 8"
            ).fetchall()
        }

        all_seeds = [dict(r) for r in conn.execute("SELECT * FROM seed_tracks").fetchall()]
        if not all_seeds:
            return

        # 1st preference: not active and not recently played
        candidates = [
            s for s in all_seeds
            if s["video_id"] not in active_or_playing and s["video_id"] not in recent_played
        ]
        # 2nd preference: not currently active/queued/playing
        if len(candidates) < needed:
            extra = [
                s for s in all_seeds
                if s["video_id"] not in active_or_playing and s not in candidates
            ]
            candidates.extend(extra)

        random.shuffle(candidates)
        for seed in candidates[:needed]:
            conn.execute(
                """
                INSERT INTO queue (
                    video_id, title, artist, duration, thumbnail,
                    requested_by_id, requested_by_name, status, votes, tie_breaker, is_seed
                ) VALUES (?, ?, ?, ?, ?, 'system_seed', '8BIT_AUTODJ', 'queued', 0, ?, 1)
                """,
                (
                    seed["video_id"],
                    seed["title"],
                    seed["artist"],
                    seed.get("duration") or 210,
                    seed.get("thumbnail") or "",
                    random.random(),
                ),
            )
        conn.commit()


def get_active_queue(min_seed_fill: int = 5) -> List[Dict[str, Any]]:
    """Returns queued tracks ordered by votes DESC, random tie_breaker DESC, topping up with seed tracks if below `min_seed_fill`."""
    if min_seed_fill > 0:
        ensure_minimum_queue_size(min_seed_fill)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM queue
            WHERE status = 'queued'
            ORDER BY votes DESC, tie_breaker DESC, id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_approvals() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM queue
            WHERE status = 'pending_approval'
            ORDER BY created_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def approve_request(queue_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM queue WHERE id = ? AND status = 'pending_approval'", (queue_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE queue SET status = 'queued', votes = 1 WHERE id = ?",
            (queue_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO votes (queue_id, client_id) VALUES (?, ?)",
            (queue_id, row["requested_by_id"]),
        )
        conn.execute(
            "UPDATE users SET requests_count = requests_count + 1 WHERE client_id = ?",
            (row["requested_by_id"],),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM queue WHERE id = ?", (queue_id,)).fetchone())


def reject_or_remove_track(queue_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE queue SET status = 'vetoed' WHERE id = ? AND status IN ('pending_approval', 'queued')",
            (queue_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def promote_track_to_top(queue_id: int) -> bool:
    """Bumps a queued track's votes/tie_breaker so it becomes #1 in the queue."""
    with get_conn() as conn:
        top = conn.execute(
            "SELECT MAX(votes) as max_v FROM queue WHERE status = 'queued'"
        ).fetchone()
        max_v = (top["max_v"] if top and top["max_v"] is not None else 1)
        cur = conn.execute(
            "UPDATE queue SET votes = ?, tie_breaker = 999999.0 WHERE id = ? AND status = 'queued'",
            (max_v + 1, queue_id),
        )
        conn.commit()
        return cur.rowcount > 0


def clear_all_cached_scenes() -> int:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM ai_scenes")
        conn.commit()
        return cur.rowcount


def get_now_playing() -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM queue WHERE status = 'playing' ORDER BY played_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def pop_next_track_to_play() -> Optional[Dict[str, Any]]:
    """
    Marks the current playing track as 'played', then selects the highest-voted
    queued track (or inserts a seed track if the queue is empty) and marks it 'playing'.
    Also increments the winning DJ's leaderboard score if not a seed track.
    """
    with get_conn() as conn:
        # Mark current playing as played
        conn.execute(
            "UPDATE queue SET status = 'played' WHERE status = 'playing'"
        )

        # Find highest voted queued track (preferring ready audio if available, otherwise top voted)
        next_row = conn.execute(
            """
            SELECT * FROM queue
            WHERE status = 'queued'
            ORDER BY votes DESC, tie_breaker DESC, id ASC
            LIMIT 1
            """
        ).fetchone()

        if next_row:
            track = dict(next_row)
            conn.execute(
                "UPDATE queue SET status = 'playing', played_at = ? WHERE id = ?",
                (time.time(), track["id"]),
            )
            if not track["is_seed"] and track["requested_by_id"] != "system_seed":
                conn.execute(
                    "UPDATE users SET wins = wins + 1 WHERE client_id = ?",
                    (track["requested_by_id"],),
                )
            conn.commit()
            return dict(conn.execute("SELECT * FROM queue WHERE id = ?", (track["id"],)).fetchone())

    # If user queue is empty, pull a track from the Host Seed Playlist
    seed = pick_next_seed_track()
    if not seed:
        return None

    res = add_to_queue(
        video_id=seed["video_id"],
        title=seed["title"],
        artist=seed["artist"],
        duration=seed["duration"],
        thumbnail=seed["thumbnail"],
        client_id="system_seed",
        status="playing",
        is_seed=True,
    )
    with get_conn() as conn:
        conn.execute(
            "UPDATE queue SET status = 'playing', played_at = ? WHERE id = ?",
            (time.time(), res["item"]["id"]),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM queue WHERE id = ?", (res["item"]["id"],)).fetchone())


def pick_next_seed_track() -> Optional[Dict[str, Any]]:
    """Picks a seed track that hasn't been played recently, or any random seed track."""
    with get_conn() as conn:
        # Check if we saved a designated next_seed_video_id in settings
        next_seed_setting = conn.execute(
            "SELECT value FROM settings WHERE key = 'next_seed_video_id'"
        ).fetchone()
        if next_seed_setting:
            seed_row = conn.execute(
                "SELECT * FROM seed_tracks WHERE video_id = ?",
                (next_seed_setting["value"],),
            ).fetchone()
            if seed_row:
                # Rotate next seed for future
                rotate_next_seed(conn, exclude_video_id=seed_row["video_id"])
                return dict(seed_row)

        return rotate_next_seed(conn)


def get_designated_next_seed() -> Optional[Dict[str, Any]]:
    """Returns the currently designated next seed song so the pre-fetcher can pre-buffer it."""
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'next_seed_video_id'").fetchone()
        if row:
            seed = conn.execute("SELECT * FROM seed_tracks WHERE video_id = ?", (row["value"],)).fetchone()
            if seed:
                return dict(seed)
        return rotate_next_seed(conn)


def rotate_next_seed(conn: sqlite3.Connection, exclude_video_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if exclude_video_id:
        rows = conn.execute(
            "SELECT * FROM seed_tracks WHERE video_id != ?", (exclude_video_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM seed_tracks").fetchall()

    if not rows:
        rows = conn.execute("SELECT * FROM seed_tracks").fetchall()
    if not rows:
        return None

    chosen = dict(random.choice(rows))
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('next_seed_video_id', ?)",
        (chosen["video_id"],),
    )
    conn.commit()
    return chosen


def get_seed_pool() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM seed_tracks ORDER BY added_at DESC, title ASC").fetchall()
        return [dict(r) for r in rows]


def replace_seed_pool(tracks: List[Dict[str, Any]], playlist_id: str) -> int:
    with get_conn() as conn:
        conn.execute("DELETE FROM seed_tracks")
        inserted = 0
        for t in tracks:
            conn.execute(
                """
                INSERT OR REPLACE INTO seed_tracks (video_id, title, artist, duration, thumbnail, playlist_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    t["video_id"],
                    t["title"],
                    t["artist"],
                    t.get("duration", 210),
                    t.get("thumbnail", ""),
                    playlist_id,
                ),
            )
            inserted += 1
        # Clear unvoted auto-DJ seed tracks from the active queue so 5 tracks from the new playlist fill in immediately
        conn.execute(
            "DELETE FROM queue WHERE status = 'queued' AND is_seed = 1 AND votes = 0"
        )
        rotate_next_seed(conn)
        conn.commit()
    ensure_minimum_queue_size(5)
    return inserted


def update_audio_status(video_id: str, status: str, audio_file: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE queue SET audio_status = ?, audio_file = ? WHERE video_id = ?",
            (status, audio_file, video_id),
        )
        conn.commit()


def get_leaderboard(limit: int = 8) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT client_id, username, wins, requests_count, votes_cast
            FROM users
            WHERE client_id != 'system_seed' AND (wins > 0 OR requests_count > 0 OR votes_cast > 0)
            ORDER BY wins DESC, votes_cast DESC, requests_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_cached_scene(video_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT scene_json FROM ai_scenes WHERE video_id = ?", (video_id,)).fetchone()
        if row:
            try:
                return json.loads(row["scene_json"])
            except Exception:
                return None
        return None


def save_cached_scene(video_id: str, title: str, artist: str, scene: Dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO ai_scenes (video_id, title, artist, scene_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (video_id, title, artist, json.dumps(scene), time.time()),
        )
        conn.commit()


def get_recently_played_video_ids(keep_recent: int = 2) -> List[str]:
    """Returns video_ids that are currently active or needed so old cached files can be cleaned up."""
    with get_conn() as conn:
        active_rows = conn.execute(
            "SELECT DISTINCT video_id FROM queue WHERE status IN ('playing', 'queued', 'pending_approval')"
        ).fetchall()
        keep = {r["video_id"] for r in active_rows}

        next_seed = conn.execute("SELECT value FROM settings WHERE key = 'next_seed_video_id'").fetchone()
        if next_seed:
            keep.add(next_seed["value"])

        recent_played = conn.execute(
            "SELECT video_id FROM queue WHERE status = 'played' ORDER BY played_at DESC LIMIT ?",
            (keep_recent,),
        ).fetchall()
        for r in recent_played:
            keep.add(r["video_id"])

        return list(keep)
