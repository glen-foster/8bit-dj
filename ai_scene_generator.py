"""
AI-Driven 8-Bit Scene & Animation Engine for the 8-Bit Jukebox MVP.
Uses the official `google-genai` SDK (`gemini-2.5-flash` model) to generate custom
retro arcade stage titles, RPG flavor logs, 4-color palettes, and 16x16 pixel-art
sprite matrices with animation behaviors based on the song's vibe and lyrics.
Includes a rich multi-theme classic fallback 8-bit arcade scene generator when
GEMINI_API_KEY is not set or offline.
"""

import asyncio
import hashlib
import json
import logging
import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from pydantic import BaseModel, Field

import database

load_dotenv()
logger = logging.getLogger("jukebox.ai")

# Attempt to import the official google-genai SDK
try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False
    logger.warning("google-genai package not installed; using classic 8-bit fallback scenes.")


class PaletteSchema(BaseModel):
    bg_primary: str = Field(description="Dark 8-bit sky/background hex color matching the song's mood/cover art")
    bg_secondary: str = Field(description="Mid-dark horizon/mountain/grid hex color harmonizing with bg_primary")
    accent_neon: str = Field(description="Signature primary color of the song/music video (high contrast hex)")
    pixel_highlight: str = Field(description="Complementary bright highlight hex color for sprites & UI borders")


class SpriteSchema(BaseModel):
    name: str = Field(description="Specific object or character from the song's lyrics/music video, e.g. 'red_corvette', 'daft_helmet', 'peach_can'")
    visual_concept: str = Field(description="1-sentence description of what this 16x16 sprite depicts and how its silhouette is structured")
    behavior: str = Field(
        description="Animation behavior matching the object: 'bounce' (jumping/bouncing), 'scroll' (vehicles/walking/flying across), 'float' (clouds/ghosts/hearts/stars), 'rain' (falling rain/coins/tears/leaves), 'dancers' (characters dancing on the floor)"
    )
    rows_ascii: List[str] = Field(
        description=(
            "Exactly 16 strings, each exactly 16 characters long, using ONLY characters '.', '1', '2', '3', '4' "
            "where '.'=transparent background, '1'=dark outline/shadow (bg_secondary), '2'=main body color (accent_neon), "
            "'3'=secondary/accent color (pixel_highlight), '4'=bright white/eyes/shine. "
            "Draw a clear, centered, recognizable 16x16 NES-style pixel art icon with a clean silhouette!"
        )
    )


class ArcadeSceneSchema(BaseModel):
    song_analysis: str = Field(
        description="Brief analysis of the song's specific lyrics, music video imagery, album cover colors, and emotional mood."
    )
    stage_title: str = Field(
        description="Clever retro 8-bit video game stage title referencing specific lyrics or imagery from the song (max 36 chars)"
    )
    flavor_text: str = Field(
        description="1-sentence 8-bit RPG battle log or arcade ticker message packed with specific lyric/artist references"
    )
    bg_style: str = Field(
        description=(
            "Retro visualizer background style best fitting this song: "
            "'side_scroller' (16-bit platformer world with clouds, blocks, and ground—great when sprites include characters, dancers, animals, or walkers), "
            "'winamp_analyzer' (classic 90s Winamp segmented LED spectrum bars with peak-hold caps + live oscilloscope waveform), "
            "'winamp_fire' (demoscene plasma/fire equalizer bars with rising ember particles—great for high-energy/rock/electronic tracks), "
            "'city_cruiser' (parallax midnight city skyline with twinkling windows & highway—great for pop, soul, R&B, funk, night drives), "
            "or 'synth_grid' (80s OutRun sunset & 3D perspective grid)."
        )
    )
    palette: PaletteSchema
    sprites: List[SpriteSchema] = Field(
        description="List of 3 distinct, recognizable 16x16 pixel-art sprites directly tied to the song's lyrics, title, or music video"
    )


# Classic 16x16 Pixel Art Sprites (0=transparent, 1=bg_secondary, 2=accent_neon, 3=pixel_highlight, 4=white)
SPRITE_SPACE_INVADER = [
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,2,0,0,0,0,0,0,0,0,2,0,0,0],
    [0,0,0,0,2,0,0,0,0,0,0,2,0,0,0,0],
    [0,0,0,2,2,2,2,2,2,2,2,2,2,0,0,0],
    [0,0,2,2,3,2,2,2,2,2,2,3,2,2,0,0],
    [0,2,2,2,4,2,2,2,2,2,2,4,2,2,2,0],
    [0,2,2,2,2,2,2,2,2,2,2,2,2,2,2,0],
    [0,2,0,2,2,2,2,2,2,2,2,2,2,0,2,0],
    [0,2,0,2,0,0,0,0,0,0,0,0,2,0,2,0],
    [0,0,0,0,2,2,0,0,0,0,2,2,0,0,0,0],
    [0,0,0,2,3,0,0,0,0,0,0,3,2,0,0,0],
    [0,0,2,3,0,0,0,0,0,0,0,0,3,2,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
]

SPRITE_PIXEL_HEART = [
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,3,3,3,0,0,0,0,0,3,3,3,0,0,0],
    [0,3,4,4,3,3,0,0,0,3,3,3,3,3,0,0],
    [3,4,4,3,3,3,3,0,3,3,3,3,3,3,3,0],
    [3,4,3,3,3,3,3,3,3,3,3,3,2,3,3,0],
    [3,3,3,3,3,3,3,3,3,3,3,3,2,3,3,0],
    [3,3,3,3,3,3,3,3,3,3,3,2,2,3,3,0],
    [0,3,3,3,3,3,3,3,3,3,2,2,3,3,0,0],
    [0,0,3,3,3,3,3,3,3,2,2,3,3,0,0,0],
    [0,0,0,3,3,3,3,3,2,2,3,3,0,0,0,0],
    [0,0,0,0,3,3,3,2,2,3,3,0,0,0,0,0],
    [0,0,0,0,0,3,3,2,3,3,0,0,0,0,0,0],
    [0,0,0,0,0,0,3,3,3,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,3,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
]

SPRITE_BOOMBOX_DANCER = [
    [0,0,0,0,0,2,2,2,2,2,2,0,0,0,0,0],
    [0,0,0,0,2,3,3,3,3,3,3,2,0,0,0,0],
    [0,0,0,0,2,3,4,3,3,4,3,2,0,0,0,0],
    [0,0,0,0,2,3,3,3,3,3,3,2,0,0,0,0],
    [0,0,0,0,0,2,2,4,4,2,2,0,0,0,0,0],
    [0,0,2,2,2,2,2,2,2,2,2,2,2,2,0,0],
    [0,2,3,2,0,2,3,2,2,3,2,0,2,3,2,0],
    [0,2,4,2,0,2,2,3,3,2,2,0,2,4,2,0],
    [0,0,2,0,0,2,2,2,2,2,2,0,0,2,0,0],
    [0,0,0,0,0,0,2,2,2,2,0,0,0,0,0,0],
    [0,0,0,0,0,2,2,0,0,2,2,0,0,0,0,0],
    [0,0,0,0,0,2,2,0,0,2,2,0,0,0,0,0],
    [0,0,0,0,2,3,2,0,0,2,3,2,0,0,0,0],
    [0,0,0,0,2,4,2,0,0,2,4,2,0,0,0,0],
    [0,0,0,2,3,3,2,0,0,2,3,3,2,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
]

SPRITE_MUSIC_NOTE = [
    [0,0,0,0,0,0,3,3,3,3,3,3,3,0,0,0],
    [0,0,0,0,0,0,3,4,4,4,4,4,3,0,0,0],
    [0,0,0,0,0,0,3,3,3,3,3,3,3,0,0,0],
    [0,0,0,0,0,0,3,2,0,0,0,3,2,0,0,0],
    [0,0,0,0,0,0,3,2,0,0,0,3,2,0,0,0],
    [0,0,0,0,0,0,3,2,0,0,0,3,2,0,0,0],
    [0,0,0,0,0,0,3,2,0,0,0,3,2,0,0,0],
    [0,0,0,0,0,0,3,2,0,0,0,3,2,0,0,0],
    [0,0,0,2,2,2,3,2,0,2,2,3,2,0,0,0],
    [0,0,2,3,4,3,3,2,2,3,4,3,2,0,0,0],
    [0,0,2,3,4,3,3,2,2,3,4,3,2,0,0,0],
    [0,0,0,2,2,2,2,0,0,2,2,2,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
]

SPRITE_SPORTS_CRUISER = [
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,2,2,2,2,2,2,0,0,0,0,0,0],
    [0,0,0,2,4,4,4,4,4,4,2,2,0,0,0,0],
    [0,0,2,3,3,3,3,3,3,3,3,3,2,2,0,0],
    [0,2,3,3,2,2,3,3,3,2,2,3,3,4,2,0],
    [0,2,4,3,2,2,3,3,3,2,2,3,3,4,2,0],
    [0,2,3,3,3,3,3,3,3,3,3,3,3,3,2,0],
    [0,0,2,1,4,1,2,2,2,2,1,4,1,2,0,0],
    [0,0,0,1,1,1,0,0,0,0,1,1,1,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
]


FALLBACK_PALETTES = [
    {
        "bg_primary": "#090518",
        "bg_secondary": "#1d1138",
        "accent_neon": "#00f5d4",
        "pixel_highlight": "#ff2a85",
    },
    {
        "bg_primary": "#05101a",
        "bg_secondary": "#0e2a38",
        "accent_neon": "#39ff14",
        "pixel_highlight": "#ffbe0b",
    },
    {
        "bg_primary": "#16070d",
        "bg_secondary": "#331022",
        "accent_neon": "#ff9e00",
        "pixel_highlight": "#00bbf9",
    },
    {
        "bg_primary": "#0b0920",
        "bg_secondary": "#24195c",
        "accent_neon": "#f72585",
        "pixel_highlight": "#4cc9f0",
    },
]


def build_fallback_scene(video_id: str, title: str, artist: str) -> Dict[str, Any]:
    """Deterministic, high-energy classic 8-bit arcade scene when Gemini API is unset or offline."""
    digest = int(hashlib.md5(f"{video_id}:{title}:{artist}".encode("utf-8")).hexdigest(), 16)
    stage_num = (digest % 99) + 1
    palette = FALLBACK_PALETTES[digest % len(FALLBACK_PALETTES)]
    clean_title = title.upper()[:24]

    flavor_options = [
        f"A wild {artist.upper()} appeared! {clean_title} deals +999 BASS DAMAGE to the dancefloor!",
        f"PLAYER 1 inserted a cosmic coin! {artist} summons the legendary 8-Bit Synth Boss!",
        f"CRITICAL HIT! {title} by {artist} boosts party stamina by +250%!",
        f"WARNING: {artist.upper()} has entered the neon cyber-grid with '{title}'!",
    ]

    bg_styles = ["side_scroller", "winamp_analyzer", "winamp_fire", "city_cruiser", "synth_grid"]
    return {
        "stage_title": f"STAGE {stage_num:02d}: {clean_title} ARENA",
        "flavor_text": flavor_options[digest % len(flavor_options)],
        "bg_style": bg_styles[digest % len(bg_styles)],
        "palette": palette,
        "sprites": [
            {
                "name": "arcade_invader",
                "behavior": "bounce",
                "matrix": SPRITE_SPACE_INVADER,
            },
            {
                "name": "cyber_dancer",
                "behavior": "dancers",
                "matrix": SPRITE_BOOMBOX_DANCER,
            },
            {
                "name": "synth_cruiser",
                "behavior": "scroll",
                "matrix": SPRITE_SPORTS_CRUISER,
            },
            {
                "name": "chiptune_heart",
                "behavior": "float",
                "matrix": SPRITE_PIXEL_HEART if digest % 2 == 0 else SPRITE_MUSIC_NOTE,
            },
        ],
        "source": "classic_fallback",
    }


def _normalize_matrix(matrix: Any, size: int = 16) -> List[List[int]]:
    """Ensures every sprite matrix is strictly 16x16 or 24x24 with integers 0-4."""
    if not isinstance(matrix, list) or len(matrix) == 0:
        return SPRITE_SPACE_INVADER

    target_size = 24 if len(matrix) >= 20 else 16
    normalized: List[List[int]] = []
    for r_idx in range(target_size):
        row = matrix[r_idx] if r_idx < len(matrix) and isinstance(matrix[r_idx], list) else []
        norm_row: List[int] = []
        for c_idx in range(target_size):
            val = row[c_idx] if c_idx < len(row) else 0
            try:
                ival = int(val)
                norm_row.append(max(0, min(4, ival)))
            except Exception:
                norm_row.append(0)
        normalized.append(norm_row)
    return normalized


def _ascii_rows_to_matrix(rows_ascii: Any, fallback_matrix: Any = None) -> List[List[int]]:
    """Converts 16x16 ASCII art rows ('.', '1', '2', '3', '4') into a 16x16 integer matrix (0-4)."""
    if isinstance(rows_ascii, list) and len(rows_ascii) > 0 and isinstance(rows_ascii[0], str):
        char_map = {".": 0, " ": 0, "0": 0, "1": 1, "2": 2, "3": 3, "4": 4}
        matrix: List[List[int]] = []
        for r_idx in range(16):
            line = rows_ascii[r_idx].strip() if r_idx < len(rows_ascii) else ""
            row: List[int] = []
            for c_idx in range(16):
                ch = line[c_idx] if c_idx < len(line) else "."
                row.append(char_map.get(ch, 0))
            matrix.append(row)
        return matrix
    return _normalize_matrix(fallback_matrix)


def _generate_with_gemini_sync(video_id: str, title: str, artist: str, api_key: str) -> Dict[str, Any]:
    client = genai.Client(api_key=api_key)

    prompt = f"""
You are a master 8-Bit Pixel Artist and Creative Visual Director.
Design a bespoke, imaginative 8-bit visual stage for:
Song Title: "{title}"
Artist: "{artist}"

CRITICAL INSTRUCTIONS FOR CREATIVITY, VARIETY & THEMATIC ACCURACY:
1. NO SPACE INVADERS, NO GENERIC ROBOTS, NO GENERIC SCI-FI (unless the song is literally about space/robots):
   - Break completely free from generic arcade cliches! Every song should feel like a totally different world, era, and genre (e.g., Motown soul club, sunny California beach, 70s disco dancefloor, rainy London street, tropical island, Western desert highway, enchanted forest, heartbroken bedroom, vibrant carnival, etc.).
   - Look closely at the song's title, lyrics, era, cultural vibe, and famous music video imagery.
   - Example: For "You Can't Hurry Love" by The Supremes -> 1960s Motown stage microphone (`dancers`), ticking vintage pocket watch / hourglass (`bounce`), fluttering love letters & hearts (`float`), warm Motown gold, velvet burgundy, and cream palette.
   - Example: For "Africa" by Toto -> falling golden rain drops (`rain`), acacia tree / elephant silhouette (`scroll`), glowing compass / southern cross star (`float`), warm savanna sunset gold & deep terracotta palette.
   - Example: For "Stayin' Alive" by Bee Gees -> glittering disco ball (`float`), platform boot strutting (`scroll`), 70s white suit dancer (`dancers`), warm disco magenta/gold/midnight palette.
2. COLOR PALETTE (`palette`):
   - Pick 4 hex colors that authentically reflect the song's era, album cover, and emotional mood:
     * `bg_primary`: Deep dark sky/stage background hex (luminance < 15% so white text is readable).
     * `bg_secondary`: Horizon/landscape/stage floor shade that harmonizes with `bg_primary`.
     * `accent_neon`: The signature primary vibrant color of this song's mood/era.
     * `pixel_highlight`: A high-contrast secondary accent color for sprites and highlights.
3. RECOGNIZABLE, EXPRESSIVE 16x16 PIXEL ART SPRITES (`sprites`):
   - Create 3 distinct, creative 16x16 sprites using `rows_ascii` (16 strings of 16 characters each) that directly illustrate specific nouns, metaphors, or scenes from "{title}" by "{artist}".
   - Use '.' for transparent background, '1' for dark outline/shading, '2' for primary color (`accent_neon`), '3' for secondary color (`pixel_highlight`), and '4' for white highlights/sparkles.
   - Reference example of a clean, recognizable 16x16 sprite in `rows_ascii` format (a retro heart):
     "................"
     "..1111....1111.."
     ".134431..133331."
     "1344333113333331"
     "1343333333332331"
     "1333333333332331"
     "1333333333322331"
     ".13333333322331."
     "..133333322331.."
     "...1333322331..."
     "....13322331...."
     ".....132331....."
     "......1331......"
     ".......11......."
     "................"
     "................"
   - Draw recognizable, well-proportioned silhouettes with outlines ('1'), filled bodies ('2'/'3'), and highlights ('4')—NEVER output random scattered dots or generic space aliens!
   - Match each sprite's `behavior` to what it actually is (`rain` for falling rain/petals/confetti/tears, `scroll` for vehicles/animals/strollers/waves, `dancers` for singers/instruments/dancers, `bounce` for rhythmic/ticking/jumping items, `float` for hearts/clouds/balloons/stars).
"""

    candidate_models = ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-flash-latest"]
    response = None
    used_model = "gemini-2.5-flash"
    last_err = None

    for model_name in candidate_models:
        try:
            cfg = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ArcadeSceneSchema,
                temperature=0.85,
            )
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=cfg,
            )
            used_model = model_name
            break
        except Exception as err:
            last_err = err
            logger.warning(f"Model {model_name} unavailable/rate-limited ({str(err)[:90]}); trying next Gemini model...")

    if response is None:
        raise RuntimeError(f"All Gemini models failed: {last_err}")

    data = json.loads(response.text)
    valid_behaviors = {"bounce", "scroll", "float", "rain", "starfield", "dancers"}

    sprites = []
    for sp in data.get("sprites", [])[:4]:
        beh = sp.get("behavior", "bounce").lower()
        if beh not in valid_behaviors:
            beh = "bounce"
        sprites.append(
            {
                "name": str(sp.get("name", "sprite"))[:24],
                "behavior": beh,
                "matrix": _ascii_rows_to_matrix(sp.get("rows_ascii"), sp.get("matrix")),
            }
        )

    if not sprites:
        sprites = build_fallback_scene(video_id, title, artist)["sprites"]

    palette = data.get("palette", {})
    valid_bg_styles = {"side_scroller", "winamp_analyzer", "winamp_fire", "city_cruiser", "synth_grid"}
    raw_bg = str(data.get("bg_style", "")).lower().strip()
    if raw_bg not in valid_bg_styles:
        has_characters = any(s.get("behavior") in ("dancers", "scroll") for s in sprites)
        raw_bg = "side_scroller" if has_characters else "winamp_analyzer"

    return {
        "stage_title": str(data.get("stage_title", f"STAGE 01: {title.upper()[:20]}"))[:42],
        "flavor_text": str(
            data.get(
                "flavor_text",
                f"{artist} unleashes {title} across the 8-bit sound system!",
            )
        )[:160],
        "bg_style": raw_bg,
        "palette": {
            "bg_primary": palette.get("bg_primary", "#090518"),
            "bg_secondary": palette.get("bg_secondary", "#1d1138"),
            "accent_neon": palette.get("accent_neon", "#00f5d4"),
            "pixel_highlight": palette.get("pixel_highlight", "#ff2a85"),
        },
        "sprites": sprites,
        "source": "gemini-2.5-flash",
        "model_used": used_model,
        "version": 3,
    }


# Track in-flight scene generation jobs to avoid duplicate API calls
_inflight_scenes: set[str] = set()


async def ensure_scene_for_track(video_id: str, title: str, artist: str) -> Dict[str, Any]:
    """
    Returns cached AI scene from SQLite if available; otherwise generates via
    Gemini (`gemini-2.5-flash`) or falls back to the classic 8-bit arcade scene,
    and caches the result in SQLite.
    """
    load_dotenv(override=True)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    has_live_key = bool(HAS_GENAI and api_key and api_key != "your_gemini_api_key_here")

    cached = database.get_cached_scene(video_id)
    if cached:
        # Upgrade fallback or older scenes to v3 creative Gemini scenes
        if not (has_live_key and (cached.get("source") == "classic_fallback" or cached.get("version") != 3)):
            return cached

    if video_id in _inflight_scenes:
        return cached or build_fallback_scene(video_id, title, artist)

    _inflight_scenes.add(video_id)
    try:
        if has_live_key:
            try:
                logger.info(f"Generating Gemini 8-bit scene for '{title}' by '{artist}' ({video_id})...")
                scene = await asyncio.to_thread(
                    _generate_with_gemini_sync, video_id, title, artist, api_key
                )
                database.save_cached_scene(video_id, title, artist, scene)
                logger.info(f"Cached Gemini 8-bit scene for '{title}': {scene.get('stage_title')}")
                return scene
            except Exception as exc:
                logger.warning(f"Gemini scene generation failed for {video_id} ({exc}); using classic fallback.")

        fallback = build_fallback_scene(video_id, title, artist)
        database.save_cached_scene(video_id, title, artist, fallback)
        return fallback
    finally:
        _inflight_scenes.discard(video_id)
