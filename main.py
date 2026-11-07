"""
FastAPI Backend for the 8-Bit Jukebox MVP.
Coordinates SQLite persistence, WebSockets telemetry, YouTube Music search,
the EBU R128 Audio Pre-fetcher, and the Gemini 2.5 Flash 8-Bit Scene Engine.
"""

import asyncio
import io
import json
import logging
import os
import re
import shutil
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import qrcode
import subprocess
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

import ai_scene_generator
import audio_engine
import database
import wifi_manager

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jukebox.main")

BASE_DIR = Path(__file__).parent
HOST_PIN = os.getenv("HOST_PIN", "1234").strip()
PORT = int(os.getenv("PORT", "8000"))

_tunnel_url: Optional[str] = None
_tunnel_proc: Optional[asyncio.subprocess.Process] = None


def is_local_device_request(client_host: Optional[str], headers: Any) -> bool:
    """
    Returns True only when accessed directly on the local device (e.g. Raspberry Pi kiosk
    at http://localhost:8000 or http://127.0.0.1:8000), and False when accessed remotely
    over LAN IP (192.168.x.x) or the public SSH reverse tunnel (*.lhr.life).
    """
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return False
    if headers.get("x-forwarded-for") or headers.get("x-forwarded-host"):
        return False
    host_hdr = (headers.get("host") or "").split(":")[0].strip().lower()
    return host_hdr in ("localhost", "127.0.0.1", "::1")


def get_system_volume() -> int:
    raw = database.get_setting("system_volume", "50")
    try:
        return max(0, min(100, int(raw)))
    except ValueError:
        return 50


def apply_os_system_volume(vol_pct: int) -> None:
    """
    Applies `vol_pct` (0-100) to the OS audio mixer:
    - On Raspberry Pi (PipeWire / WirePlumber): updates WirePlumber saved route volume AND
      sets live `@DEFAULT_AUDIO_SINK@` volume via `wpctl` (plus `pactl` / `amixer` if present).
    - On macOS: sets output volume via `osascript`.
    """
    vol_pct = max(0, min(100, int(vol_pct)))
    vol_float = round(vol_pct / 100.0, 2)
    cubic_vol = round((vol_pct / 100.0) ** 3, 6)
    is_muted = vol_pct == 0

    try:
        if sys.platform.startswith("linux"):
            # 1. Update WirePlumber persisted HDMI route volume so hotplugged HDMI TVs also use vol_pct
            wp_dir = Path.home() / ".local" / "state" / "wireplumber"
            default_routes = wp_dir / "default-routes"
            if default_routes.exists():
                try:
                    content = default_routes.read_text(encoding="utf-8")
                    content = re.sub(
                        r'"channelVolumes":\[[^\]]+\]',
                        f'"channelVolumes":[{cubic_vol}, {cubic_vol}]',
                        content,
                    )
                    content = re.sub(
                        r'"mute":(true|false)',
                        f'"mute":{"true" if is_muted else "false"}',
                        content,
                    )
                    default_routes.write_text(content, encoding="utf-8")
                except Exception as exc:
                    logger.debug(f"WirePlumber default-routes update warning: {exc}")

            # 2. Apply live PipeWire volume via wpctl across user runtime session(s)
            wpctl_bin = shutil.which("wpctl")
            if wpctl_bin:
                uid = os.getuid()
                runtime_dirs = [f"/run/user/{uid}", "/run/user/1000"]
                for rdir in dict.fromkeys(runtime_dirs):
                    if Path(rdir).exists():
                        env = dict(os.environ, XDG_RUNTIME_DIR=rdir)
                        subprocess.run(
                            [wpctl_bin, "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if is_muted else "0"],
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=3,
                        )
                        subprocess.run(
                            [wpctl_bin, "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol_float:.2f}"],
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=3,
                        )

            pactl_bin = shutil.which("pactl")
            if pactl_bin:
                uid = os.getuid()
                env = dict(os.environ, XDG_RUNTIME_DIR=f"/run/user/{uid}")
                subprocess.run(
                    [pactl_bin, "set-sink-volume", "@DEFAULT_SINK@", f"{vol_pct}%"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
        elif sys.platform == "darwin":
            osascript_bin = shutil.which("osascript")
            if osascript_bin:
                subprocess.run(
                    [osascript_bin, "-e", f"set volume output volume {vol_pct}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
    except Exception as exc:
        logger.warning(f"System volume apply warning: {exc}")


def set_system_volume(vol_pct: int) -> int:
    clamped = max(0, min(100, int(vol_pct)))
    database.set_setting("system_volume", str(clamped))
    apply_os_system_volume(clamped)
    return clamped


def manage_kiosk_window(mode: str = "minimize") -> bool:
    """
    Controls the Raspberry Pi Wayland (labwc) Chromium kiosk window via `wlrctl`:
    - `mode == 'minimize'`: minimizes Chromium so the Pi desktop & Wi-Fi bar are visible.
    - `mode == 'fullscreen'`: focuses and restores Chromium to fullscreen.
    """
    wlrctl_bin = shutil.which("wlrctl")
    if not wlrctl_bin:
        return False
    uid = os.getuid()
    runtime_dirs = [f"/run/user/{uid}", "/run/user/1000"]
    match_args = ["app_id:chromium", "app_id:chrome-localhost__bigscreen-Default"]
    ok = False
    for rdir in dict.fromkeys(runtime_dirs):
        if Path(rdir).exists():
            env = dict(os.environ, XDG_RUNTIME_DIR=rdir, WAYLAND_DISPLAY="wayland-0")
            try:
                if mode == "minimize":
                    res = subprocess.run(
                        [wlrctl_bin, "toplevel", "minimize", *match_args],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=3,
                    )
                    ok = ok or (res.returncode == 0)
                elif mode == "fullscreen":
                    subprocess.run(
                        [wlrctl_bin, "toplevel", "focus", *match_args],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=3,
                    )
                    res = subprocess.run(
                        [wlrctl_bin, "toplevel", "fullscreen", *match_args],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=3,
                    )
                    ok = ok or (res.returncode == 0)
            except Exception as exc:
                logger.debug(f"wlrctl ({mode}) warning: {exc}")
    return ok


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_public_mobile_url(request: Optional[Request] = None) -> str:
    env_url = os.getenv("PUBLIC_URL", "").strip()
    if env_url:
        return env_url
    if _tunnel_url:
        return f"{_tunnel_url}/mobile"
    lan_ip = get_lan_ip()
    if request and request.headers.get("host"):
        host_header = request.headers["host"]
        if not host_header.startswith("localhost") and not host_header.startswith("127.0.0.1"):
            scheme = request.url.scheme or "http"
            return f"{scheme}://{host_header}/mobile"
    return f"http://{lan_ip}:{PORT}/mobile"


async def start_public_tunnel() -> None:
    """
    Launches a free outbound HTTPS reverse tunnel over SSH (`localhost.run`)
    so mobile users can scan the QR code and join over 5G/LTE or any Wi-Fi
    without router port-forwarding or TLS inspection issues.
    """
    global _tunnel_url, _tunnel_proc
    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        logger.info("ssh not found; using LAN IP for QR code.")
        return

    while True:
        try:
            logger.info("[Tunnel] Starting outbound HTTPS tunnel via localhost.run...")
            _tunnel_proc = await asyncio.create_subprocess_exec(
                ssh_bin,
                "-T",
                "-o", "StrictHostKeyChecking=no",
                "-o", "ServerAliveInterval=30",
                "-o", "ExitOnForwardFailure=yes",
                "-R", f"80:127.0.0.1:{PORT}",
                "nokey@localhost.run",
                "--",
                "--output", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.lhr\.life")
            assert _tunnel_proc.stdout is not None
            while True:
                line_bytes = await _tunnel_proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="ignore")
                match = url_pattern.search(line)
                if match:
                    new_url = match.group(0)
                    if _tunnel_url != new_url:
                        _tunnel_url = new_url
                        logger.info(f"[Tunnel] PUBLIC HTTPS URL ACTIVE: {_tunnel_url}/mobile")
                        await broadcast_state()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"[Tunnel] SSH tunnel error: {exc}")
        await asyncio.sleep(3)


class ConnectionManager:
    def __init__(self) -> None:
        # websocket -> {"role": "mobile"|"bigscreen"|"host", "client_id": str}
        self.active: Dict[WebSocket, Dict[str, str]] = {}

    async def connect(self, ws: WebSocket, role: str, client_id: str) -> None:
        await ws.accept()
        self.active[ws] = {"role": role, "client_id": client_id}

    def disconnect(self, ws: WebSocket) -> None:
        self.active.pop(ws, None)

    def get_telemetry(self) -> Dict[str, int]:
        mobile_ids = set()
        bigscreens = 0
        hosts = 0
        for meta in self.active.values():
            role = meta.get("role", "mobile")
            if role == "bigscreen":
                bigscreens += 1
            elif role == "host":
                hosts += 1
            else:
                mobile_ids.add(meta.get("client_id", ""))
        return {
            "total_connections": len(self.active),
            "active_djs": len(mobile_ids),
            "bigscreens": bigscreens,
            "hosts": hosts,
        }

    async def broadcast_json(self, payload: Dict[str, Any]) -> None:
        dead: List[WebSocket] = []
        for ws in list(self.active.keys()):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = ConnectionManager()
prefetch_engine: Optional[audio_engine.AudioPrefetchEngine] = None


def enrich_track_with_scene_and_audio(track: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not track:
        return None
    item = dict(track)
    vid = item["video_id"]
    cached_audio = audio_engine.get_cached_audio_path(vid)
    item["audio_ready"] = cached_audio is not None
    item["audio_url"] = f"/api/audio/{vid}"
    scene = database.get_cached_scene(vid)
    if not scene:
        scene = ai_scene_generator.build_fallback_scene(vid, item["title"], item["artist"])
    item["scene"] = scene
    return item


def build_full_state(client_id: Optional[str] = None, include_host: bool = False) -> Dict[str, Any]:
    now_playing = database.get_now_playing()
    if not now_playing:
        now_playing = database.pop_next_track_to_play()

    enriched_now = enrich_track_with_scene_and_audio(now_playing)
    queue = [enrich_track_with_scene_and_audio(t) for t in database.get_active_queue()]
    next_seed = enrich_track_with_scene_and_audio(database.get_designated_next_seed())

    # Determine the immediate next track for dual-deck preloading on bigscreen
    next_up_track = queue[0] if queue else next_seed

    state: Dict[str, Any] = {
        "type": "state_sync",
        "server_time": time.time(),
        "public_url": get_public_mobile_url(),
        "wifi_status": wifi_manager.get_wifi_status(),
        "system_volume": get_system_volume(),
        "now_playing": enriched_now,
        "next_up_track": next_up_track,
        "queue": queue,
        "leaderboard": database.get_leaderboard(limit=8),
        "seed_pool": database.get_seed_pool(),
        "telemetry": ws_manager.get_telemetry(),
    }

    if client_id:
        state["user"] = database.ensure_user(client_id)
        state["voted_queue_ids"] = database.get_client_voted_ids(client_id)

    if include_host:
        state["pending_approvals"] = database.get_pending_approvals()

    return state


async def broadcast_state() -> None:
    base_state = build_full_state(include_host=True)
    dead: List[WebSocket] = []
    for ws, meta in list(ws_manager.active.items()):
        try:
            cid = meta.get("client_id")
            role = meta.get("role", "mobile")
            payload = dict(base_state)
            if role != "host":
                payload.pop("pending_approvals", None)
            if cid and role == "mobile":
                payload["user"] = database.ensure_user(cid)
                payload["voted_queue_ids"] = database.get_client_voted_ids(cid)
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_manager.disconnect(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global prefetch_engine, _tunnel_proc
    database.init_db()
    # Initialize default system volume to 50% on startup so audio never starts too loud
    await asyncio.to_thread(set_system_volume, 50)
    # Ensure a track is ready in Now Playing on boot
    now = database.get_now_playing()
    if not now:
        database.pop_next_track_to_play()

    prefetch_engine = audio_engine.AudioPrefetchEngine(on_state_change=broadcast_state)
    await prefetch_engine.start()
    tunnel_task = asyncio.create_task(start_public_tunnel())
    wifi_task = asyncio.create_task(wifi_manager.wifi_watchdog_loop(on_state_change=broadcast_state))
    yield
    wifi_task.cancel()
    tunnel_task.cancel()
    if _tunnel_proc and _tunnel_proc.returncode is None:
        try:
            _tunnel_proc.terminate()
        except Exception:
            pass
    if prefetch_engine:
        await prefetch_engine.stop()


app = FastAPI(title="8-Bit Jukebox MVP", lifespan=lifespan)


# --- Request Models ---
class RegisterUserRequest(BaseModel):
    client_id: str
    reroll: bool = False


class QueueTrackRequest(BaseModel):
    client_id: str
    video_id: str
    title: str
    artist: str
    duration: int = 210
    thumbnail: str = ""
    mode: str = "fast_track"  # "fast_track" or "open_search"


class VoteRequest(BaseModel):
    client_id: str
    queue_id: int


class HostPinVerifyRequest(BaseModel):
    pin: Optional[str] = ""


class HostActionRequest(BaseModel):
    pin: Optional[str] = ""
    action: str  # "veto_skip", "toggle_pause", "seek_ff", "seek_rw", "restart_track", "approve", "reject", "load_playlist"
    queue_id: Optional[int] = None
    playlist_id: Optional[str] = None
    seconds: Optional[int] = 15


class WifiConnectRequest(BaseModel):
    ssid: str
    password: str = ""
    pin: Optional[str] = None


class WifiHotspotActionRequest(BaseModel):
    pin: Optional[str] = ""
    enable: bool = True


def verify_host_pin(pin: Optional[str], request: Optional[Request] = None) -> None:
    if request is not None:
        client_host = request.client.host if request.client else None
        if is_local_device_request(client_host, request.headers):
            return
    if not pin or pin.strip() != HOST_PIN:
        raise HTTPException(status_code=403, detail="INVALID HOST PIN! ACCESS DENIED.")


# --- Captive Portal Middleware & Routes (Hotel-Style Wi-Fi Pop-Up) ---
CAPTIVE_PORTAL_PATHS = {
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/generate_204",
    "/gen_204",
    "/ncsi.txt",
    "/connecttest.txt",
    "/redirect",
    "/canonical.html",
    "/success.txt",
}


@app.middleware("http")
async def captive_portal_intercept_middleware(request: Request, call_next):
    path = request.url.path
    host_hdr = (request.headers.get("host") or "").split(":")[0].lower()

    # 1. Explicit OS captive portal probe paths -> redirect to our 8-Bit Wi-Fi Setup page
    if path in CAPTIVE_PORTAL_PATHS:
        return RedirectResponse(url=f"http://{wifi_manager.HOTSPOT_IP}:{PORT}/wifi-setup", status_code=302)

    # 2. Any wildcard DNS domain intercepted while phone is on 8BIT-JUKEBOX-SETUP hotspot
    is_local_or_tunnel = (
        not host_hdr
        or host_hdr in ("localhost", "127.0.0.1", wifi_manager.HOTSPOT_IP)
        or host_hdr.startswith("192.168.")
        or host_hdr.startswith("10.")
        or host_hdr.endswith(".local")
        or host_hdr.endswith(".lhr.life")
    )
    if not is_local_or_tunnel and not path.startswith("/api/"):
        return RedirectResponse(url=f"http://{wifi_manager.HOTSPOT_IP}:{PORT}/wifi-setup", status_code=302)

    return await call_next(request)


# --- Frontend HTML Routes ---
@app.get("/", response_class=HTMLResponse)
@app.get("/mobile", response_class=HTMLResponse)
@app.get("/mobile.html", response_class=HTMLResponse)
async def serve_mobile(request: Request) -> Response:
    # If the Pi is currently in Wi-Fi Setup Hotspot mode and the user visits "/", show Wi-Fi Setup
    if request.url.path == "/":
        st = wifi_manager.get_wifi_status()
        if st.get("hotspot_active"):
            return FileResponse(BASE_DIR / "wifi_setup.html")
    return FileResponse(BASE_DIR / "mobile.html")


@app.get("/wifi-setup", response_class=HTMLResponse)
@app.get("/wifi_setup.html", response_class=HTMLResponse)
async def serve_wifi_setup() -> FileResponse:
    return FileResponse(BASE_DIR / "wifi_setup.html")


# --- Wi-Fi & Hotspot API ---
@app.get("/api/wifi/networks")
async def api_wifi_networks(rescan: int = Query(default=0)) -> Dict[str, Any]:
    status = await asyncio.to_thread(wifi_manager.get_wifi_status)
    networks = await asyncio.to_thread(wifi_manager.scan_wifi_networks, bool(rescan))
    return {"ok": True, "status": status, "networks": networks}


@app.post("/api/wifi/connect")
async def api_wifi_connect(req: WifiConnectRequest, request: Request) -> Dict[str, Any]:
    global _tunnel_url, _tunnel_proc
    st = await asyncio.to_thread(wifi_manager.get_wifi_status)
    # Require Host PIN only when not in captive-portal hotspot mode and not on local device
    if not st.get("hotspot_active"):
        verify_host_pin(req.pin, request)

    res = await asyncio.to_thread(wifi_manager.connect_to_wifi, req.ssid, req.password)
    if res.get("ok"):
        # Restart public HTTPS tunnel so it binds immediately over the new Wi-Fi connection
        _tunnel_url = None
        if _tunnel_proc and _tunnel_proc.returncode is None:
            try:
                _tunnel_proc.terminate()
            except Exception:
                pass
    await broadcast_state()
    return res


@app.post("/api/wifi/hotspot")
async def api_wifi_hotspot(req: WifiHotspotActionRequest, request: Request) -> Dict[str, Any]:
    verify_host_pin(req.pin, request)
    if req.enable:
        res = await asyncio.to_thread(wifi_manager.start_setup_hotspot)
    else:
        res = await asyncio.to_thread(wifi_manager.stop_setup_hotspot)
    await broadcast_state()
    return res


@app.get("/bigscreen", response_class=HTMLResponse)
@app.get("/bigscreen.html", response_class=HTMLResponse)
async def serve_bigscreen() -> FileResponse:
    return FileResponse(BASE_DIR / "bigscreen.html")


@app.get("/host", response_class=HTMLResponse)
@app.get("/host.html", response_class=HTMLResponse)
async def serve_host() -> FileResponse:
    return FileResponse(BASE_DIR / "host.html")


# --- QR Code Endpoint ---
@app.get("/api/qrcode")
async def generate_qrcode(request: Request, url: Optional[str] = None) -> Response:
    target_url = url or get_public_mobile_url(request)
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#00f5d4", back_color="#090518")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


# --- State & User API ---
@app.get("/api/state")
async def api_get_state(
    request: Request,
    client_id: Optional[str] = Query(default=None),
    x_host_pin: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    client_host = request.client.host if request.client else None
    is_local = is_local_device_request(client_host, request.headers)
    include_host = is_local or bool(x_host_pin and x_host_pin.strip() == HOST_PIN)
    state = build_full_state(client_id=client_id, include_host=include_host)
    state["public_url"] = get_public_mobile_url(request)
    return state


@app.post("/api/user")
async def api_user(req: RegisterUserRequest) -> Dict[str, Any]:
    if req.reroll:
        user = database.reroll_username(req.client_id)
        await broadcast_state()
    else:
        user = database.ensure_user(req.client_id)
    return {"ok": True, "user": user}


# --- Search & Queue API ---
@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=1)) -> Dict[str, Any]:
    results = await asyncio.to_thread(audio_engine.search_ytmusic, q, 12)
    return {"ok": True, "results": results}


@app.post("/api/request")
async def api_request_track(req: QueueTrackRequest) -> Dict[str, Any]:
    # Fast Track songs go straight to the active voting queue; Open Search goes to Host Approval
    seed_ids = {t["video_id"] for t in database.get_seed_pool()}
    if req.mode == "fast_track" or req.video_id in seed_ids:
        target_status = "queued"
    else:
        target_status = "pending_approval"

    res = database.add_to_queue(
        video_id=req.video_id,
        title=req.title,
        artist=req.artist,
        duration=req.duration,
        thumbnail=req.thumbnail,
        client_id=req.client_id,
        status=target_status,
        is_seed=False,
    )

    if prefetch_engine:
        prefetch_engine.trigger_Pass()

    # Check if this track is in Top 3 of queue and trigger async scene generation right away
    active_q = database.get_active_queue()
    top3_ids = {t["video_id"] for t in active_q[:3]}
    if req.video_id in top3_ids:
        asyncio.create_task(
            ai_scene_generator.ensure_scene_for_track(req.video_id, req.title, req.artist)
        )

    await broadcast_state()
    return {
        "ok": True,
        "already_queued": res["already_queued"],
        "status": target_status,
        "item": res["item"],
    }


@app.post("/api/vote")
async def api_vote(req: VoteRequest) -> Dict[str, Any]:
    res = database.cast_vote(req.queue_id, req.client_id)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["reason"])

    if prefetch_engine:
        prefetch_engine.trigger_Pass()

    await broadcast_state()
    return res


# --- Audio Streaming & Deck Advancement ---
@app.get("/api/audio/{video_id}")
async def serve_normalized_audio(video_id: str) -> FileResponse:
    """
    Serves the EBU R128 loudness-normalized MP3 from `/tmp/audio_cache/`.
    If not yet cached, triggers immediate synchronous normalization so the deck never 404s.
    """
    await asyncio.to_thread(apply_os_system_volume, get_system_volume())
    cached = audio_engine.get_cached_audio_path(video_id)
    if not cached and prefetch_engine:
        cached = await prefetch_engine.ensure_track_buffered(video_id, wait_if_inflight=True)
    if not cached or not cached.exists():
        raise HTTPException(status_code=404, detail="Normalized audio not available")

    media_type = "audio/wav" if cached.suffix == ".wav" else "audio/mpeg"
    return FileResponse(
        path=cached,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )


@app.post("/api/advance")
async def api_advance_track() -> Dict[str, Any]:
    """
    Called by `bigscreen.html` when the 6-second crossfade to the next deck starts
    (or when a track finishes), advancing the SQLite queue and waking the pre-fetcher.
    """
    next_track = database.pop_next_track_to_play()
    if prefetch_engine:
        prefetch_engine.trigger_Pass()
    await broadcast_state()
    return {
        "ok": True,
        "now_playing": enrich_track_with_scene_and_audio(next_track),
    }


# --- Host PIN & Admin Routes ---
@app.post("/api/host/verify")
async def api_host_verify(req: HostPinVerifyRequest, request: Request) -> Dict[str, Any]:
    verify_host_pin(req.pin, request)
    return {"ok": True, "state": build_full_state(include_host=True)}


@app.get("/api/host/search_playlists")
async def api_host_search_playlists(
    request: Request,
    q: str = Query(..., min_length=1),
    pin: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    verify_host_pin(pin, request)
    results = await asyncio.to_thread(audio_engine.search_playlists_ytmusic, q, 8)
    return {"ok": True, "results": results}


@app.post("/api/host/action")
async def api_host_action(req: HostActionRequest, request: Request) -> Dict[str, Any]:
    verify_host_pin(req.pin, request)

    if req.action == "veto_skip":
        next_track = database.pop_next_track_to_play()
        if prefetch_engine:
            prefetch_engine.trigger_Pass()
        await ws_manager.broadcast_json(
            {
                "type": "host_veto_crossfade",
                "now_playing": enrich_track_with_scene_and_audio(next_track),
            }
        )
        await broadcast_state()
        return {"ok": True, "message": "VETO TRIGGERED! 6s CROSSFADE INITIATED."}

    elif req.action in ("toggle_pause", "seek_ff", "seek_rw", "restart_track"):
        delta = req.seconds or 15
        await ws_manager.broadcast_json(
            {
                "type": "host_transport_control",
                "control": req.action,
                "seconds": delta,
            }
        )
        labels = {
            "toggle_pause": "PLAY / PAUSE TOGGLED ON BIG SCREEN!",
            "seek_ff": f"FAST-FORWARDED +{delta}s!",
            "seek_rw": f"REWOUND -{delta}s!",
            "restart_track": "TRACK RESTARTED FROM 00:00!",
        }
        return {"ok": True, "message": labels.get(req.action, "TRANSPORT COMMAND SENT")}

    elif req.action == "approve" and req.queue_id is not None:
        item = database.approve_request(req.queue_id)
        if not item:
            raise HTTPException(status_code=404, detail="Request not found")
        if prefetch_engine:
            prefetch_engine.trigger_Pass()
        await broadcast_state()
        return {"ok": True, "item": item}

    elif req.action == "reject" and req.queue_id is not None:
        ok = database.reject_or_remove_track(req.queue_id)
        if prefetch_engine:
            prefetch_engine.trigger_Pass()
        await broadcast_state()
        return {"ok": ok}

    elif req.action == "promote_top" and req.queue_id is not None:
        ok = database.promote_track_to_top(req.queue_id)
        if prefetch_engine:
            prefetch_engine.trigger_Pass()
        await broadcast_state()
        return {"ok": ok, "message": "TRACK PROMOTED TO #1 UP NEXT!"}

    elif req.action.startswith("set_viz_mode:"):
        viz_mode = req.action.split(":", 1)[1].strip()
        await ws_manager.broadcast_json(
            {
                "type": "host_transport_control",
                "control": "set_viz_mode",
                "viz_mode": viz_mode,
            }
        )
        return {"ok": True, "message": f"BIG SCREEN VISUALIZER SET TO: {viz_mode.upper()}"}

    elif req.action.startswith("set_volume:"):
        try:
            vol_pct = int(req.action.split(":", 1)[1].strip())
        except ValueError:
            vol_pct = 50
        vol_pct = await asyncio.to_thread(set_system_volume, vol_pct)
        await ws_manager.broadcast_json(
            {
                "type": "host_transport_control",
                "control": "set_volume",
                "volume_pct": vol_pct,
            }
        )
        return {"ok": True, "volume_pct": vol_pct, "message": f"SYSTEM VOLUME SET TO: {vol_pct}%"}

    elif req.action == "clear_scene_cache":
        cleared = database.clear_all_cached_scenes()
        if prefetch_engine:
            prefetch_engine.trigger_Pass()
        await broadcast_state()
        return {"ok": True, "message": f"CLEARED {cleared} CACHED AI SCENES! REGENERATING..."}

    elif req.action == "minimize_kiosk":
        await asyncio.to_thread(manage_kiosk_window, "minimize")
        await ws_manager.broadcast_json(
            {
                "type": "host_transport_control",
                "control": "exit_fullscreen",
            }
        )
        return {"ok": True, "message": "KIOSK MINIMIZED TO RASPBERRY PI DESKTOP!"}

    elif req.action == "fullscreen_kiosk":
        await asyncio.to_thread(manage_kiosk_window, "fullscreen")
        return {"ok": True, "message": "RESTORED BIG SCREEN TO FULLSCREEN!"}

    elif req.action == "load_playlist" and req.playlist_id:
        try:
            tracks = await asyncio.to_thread(
                audio_engine.load_playlist_from_ytmusic, req.playlist_id
            )
            if not tracks:
                raise HTTPException(status_code=400, detail="No tracks found in playlist.")
            count = database.replace_seed_pool(tracks, req.playlist_id)
            if prefetch_engine:
                prefetch_engine.trigger_Pass()
            await broadcast_state()
            return {"ok": True, "count": count, "message": f"LOADED {count} TRACKS INTO SEED POOL!"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Playlist load failed: {exc}")

    raise HTTPException(status_code=400, detail=f"Unknown host action: {req.action}")


# --- WebSocket Endpoint ---
@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    role: str = Query(default="mobile"),
    client_id: str = Query(default="anon"),
    pin: Optional[str] = Query(default=None),
) -> None:
    ws_client_host = websocket.client.host if websocket.client else None
    is_local_ws = is_local_device_request(ws_client_host, websocket.headers)
    if role == "host" and not is_local_ws and (not pin or pin.strip() != HOST_PIN):
        await websocket.close(code=1008, reason="Invalid Host PIN")
        return

    await ws_manager.connect(websocket, role=role, client_id=client_id)
    try:
        # Send immediate state sync upon connect
        await websocket.send_json(
            build_full_state(
                client_id=client_id if role == "mobile" else None,
                include_host=(role == "host"),
            )
        )
        # Notify all clients of updated connection telemetry
        await broadcast_state()

        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            mtype = msg.get("type")

            if mtype == "ping" or mtype == "sync":
                await websocket.send_json(
                    build_full_state(
                        client_id=client_id if role == "mobile" else None,
                        include_host=(role == "host"),
                    )
                )

            elif mtype == "vote":
                qid = int(msg.get("queue_id", 0))
                res = database.cast_vote(qid, client_id)
                if res["ok"]:
                    if prefetch_engine:
                        prefetch_engine.trigger_Pass()
                    await broadcast_state()
                else:
                    await websocket.send_json({"type": "error", "message": res["reason"]})

            elif mtype == "reroll_name":
                user = database.reroll_username(client_id)
                await websocket.send_json({"type": "user_updated", "user": user})
                await broadcast_state()

            elif mtype == "host_command":
                cmd_pin = str(msg.get("pin", "")).strip()
                if not is_local_ws and cmd_pin != HOST_PIN:
                    await websocket.send_json({"type": "error", "message": "INVALID HOST PIN!"})
                    continue

                action = msg.get("action")
                if action == "veto_skip":
                    next_track = database.pop_next_track_to_play()
                    if prefetch_engine:
                        prefetch_engine.trigger_Pass()
                    await ws_manager.broadcast_json(
                        {
                            "type": "host_veto_crossfade",
                            "now_playing": enrich_track_with_scene_and_audio(next_track),
                        }
                    )
                    await broadcast_state()
                elif action == "approve":
                    database.approve_request(int(msg.get("queue_id", 0)))
                    if prefetch_engine:
                        prefetch_engine.trigger_Pass()
                    await broadcast_state()
                elif action == "reject":
                    database.reject_or_remove_track(int(msg.get("queue_id", 0)))
                    if prefetch_engine:
                        prefetch_engine.trigger_Pass()
                    await broadcast_state()

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug(f"WebSocket closed ({client_id}): {exc}")
    finally:
        ws_manager.disconnect(websocket)
        await broadcast_state()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
