"""
Headless Wi-Fi & Captive Portal Hotspot Manager for Raspberry Pi (NetworkManager / nmcli).
- Monitors network connectivity in the background.
- If the Pi boots at a new venue with no known Wi-Fi (and no Ethernet) for ~30 seconds,
  automatically starts an open Wi-Fi Hotspot (`8BIT-JUKEBOX-SETUP` at `10.42.0.1`) with
  wildcard DNS (`address=/#/10.42.0.1`) and port 80 -> 8000 redirect so phones
  automatically open the Hotel-style Captive Portal Wi-Fi Setup screen.
- Caches nearby Wi-Fi scan results prior to enabling AP mode so the popup lists
  all surrounding venue networks immediately.
"""

import asyncio
import logging
import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger("jukebox.wifi")

HOTSPOT_CON_NAME = "jukebox-setup-ap"
HOTSPOT_SSID = os.getenv("SETUP_AP_SSID", "8BIT-JUKEBOX-SETUP")
HOTSPOT_IP = "10.42.0.1"
PORT = int(os.getenv("PORT", "8000"))

_cached_networks: List[Dict[str, Any]] = []
_last_scan_time: float = 0.0
_connecting_in_progress: bool = False


def is_linux_with_nmcli() -> bool:
    return platform.system() == "Linux" and shutil.which("nmcli") is not None


def _run_cmd(cmd: List[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def get_wifi_status() -> Dict[str, Any]:
    """Returns current Wi-Fi / Ethernet / Hotspot status."""
    status: Dict[str, Any] = {
        "supported": is_linux_with_nmcli(),
        "hotspot_active": False,
        "connected_ssid": None,
        "eth_connected": False,
        "setup_ssid": HOTSPOT_SSID,
        "setup_ip": HOTSPOT_IP,
        "wifi_qr_payload": f"WIFI:S:{HOTSPOT_SSID};T:nopass;;",
    }
    if not status["supported"]:
        return status

    try:
        res = _run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"], timeout=5)
        for line in res.stdout.splitlines():
            parts = line.strip().split(":")
            if len(parts) < 4:
                continue
            dev, dtype, state, conn = parts[0], parts[1], parts[2], ":".join(parts[3:])
            if dtype == "ethernet" and state == "connected":
                status["eth_connected"] = True
            elif dtype == "wifi" and state == "connected":
                if conn == HOTSPOT_CON_NAME or conn == HOTSPOT_SSID:
                    status["hotspot_active"] = True
                else:
                    status["connected_ssid"] = conn
    except Exception as exc:
        logger.warning(f"get_wifi_status error: {exc}")

    return status


def scan_wifi_networks(force_rescan: bool = False) -> List[Dict[str, Any]]:
    """
    Scans nearby Wi-Fi networks via `nmcli` and caches the deduplicated list.
    If the hotspot is currently active and `force_rescan` is False, returns the cached
    scan list if available and supplements with `nmcli dev wifi list`.
    """
    global _cached_networks, _last_scan_time

    if not is_linux_with_nmcli():
        return [
            {"ssid": "Venue_Guest_WiFi", "signal": 88, "security": "WPA2", "in_use": False},
            {"ssid": "Party_5G", "signal": 74, "security": "WPA2", "in_use": False},
        ]

    st = get_wifi_status()
    if force_rescan and not st["hotspot_active"]:
        try:
            _run_cmd(["sudo", "-n", "nmcli", "dev", "wifi", "rescan", "ifname", "wlan0"], timeout=10)
        except Exception:
            pass

    try:
        res = _run_cmd(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi", "list", "ifname", "wlan0"],
            timeout=8,
        )
        by_ssid: Dict[str, Dict[str, Any]] = {}
        # Start with cached networks if we are in AP mode (since AP mode can hide other channels)
        if st["hotspot_active"] and _cached_networks:
            for item in _cached_networks:
                by_ssid[item["ssid"]] = dict(item)

        for raw_line in res.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # Parse from the right because SSID can contain escaped colons
            parts = line.rsplit(":", 3)
            if len(parts) < 4:
                continue
            ssid_raw, sig_str, sec_str, in_use_str = parts[0], parts[1], parts[2], parts[3]
            ssid = ssid_raw.replace("\\:", ":").strip()
            if not ssid or ssid == "--" or ssid == HOTSPOT_SSID:
                continue
            try:
                signal = int(sig_str)
            except ValueError:
                signal = 50
            in_use = in_use_str.strip() == "*"
            sec = sec_str.strip() or "OPEN"

            existing = by_ssid.get(ssid)
            if not existing or signal > existing["signal"] or in_use:
                by_ssid[ssid] = {
                    "ssid": ssid,
                    "signal": signal,
                    "security": sec,
                    "in_use": in_use,
                }

        networks = sorted(
            by_ssid.values(),
            key=lambda x: (1 if x["in_use"] else 0, x["signal"]),
            reverse=True,
        )
        if networks:
            _cached_networks = networks
            _last_scan_time = time.time()
        return _cached_networks
    except Exception as exc:
        logger.warning(f"scan_wifi_networks error: {exc}")
        return _cached_networks


def ensure_captive_portal_system_rules() -> None:
    """
    Ensures NetworkManager's shared dnsmasq has wildcard DNS (`address=/#/10.42.0.1`)
    and port 80 on `wlan0` (`10.42.0.1:80`) redirects to our FastAPI port (`8000`).
    """
    if not is_linux_with_nmcli():
        return

    try:
        # 1. Configure wildcard DNS for NetworkManager shared hotspot mode
        conf_dir = "/etc/NetworkManager/dnsmasq-shared.d"
        conf_file = f"{conf_dir}/jukebox-captive.conf"
        conf_content = f"address=/#/{HOTSPOT_IP}\n"
        _run_cmd(["sudo", "-n", "mkdir", "-p", conf_dir], timeout=5)
        _run_cmd(
            ["sudo", "-n", "bash", "-c", f"echo '{conf_content.strip()}' > {conf_file}"],
            timeout=5,
        )

        # 2. Redirect TCP port 80 arriving on 10.42.0.1 to FastAPI PORT (8000)
        check_rule = _run_cmd(
            [
                "sudo", "-n", "iptables", "-t", "nat", "-C", "PREROUTING",
                "-d", HOTSPOT_IP, "-p", "tcp", "--dport", "80",
                "-j", "REDIRECT", "--to-ports", str(PORT),
            ],
            timeout=5,
        )
        if check_rule.returncode != 0:
            _run_cmd(
                [
                    "sudo", "-n", "iptables", "-t", "nat", "-A", "PREROUTING",
                    "-d", HOTSPOT_IP, "-p", "tcp", "--dport", "80",
                    "-j", "REDIRECT", "--to-ports", str(PORT),
                ],
                timeout=5,
            )
    except Exception as exc:
        logger.warning(f"Failed to configure captive portal rules: {exc}")


def start_setup_hotspot() -> Dict[str, Any]:
    """
    Scans surrounding Wi-Fi networks first (to cache them while in client mode),
    then activates the open `8BIT-JUKEBOX-SETUP` hotspot on `wlan0` (`10.42.0.1`).
    """
    if not is_linux_with_nmcli():
        return {"ok": False, "message": "Hotspot mode requires Linux NetworkManager (nmcli)."}

    logger.info(f"[Wi-Fi] Scanning networks before starting setup hotspot '{HOTSPOT_SSID}'...")
    scan_wifi_networks(force_rescan=True)
    ensure_captive_portal_system_rules()

    # Delete any stale connection profile with the same name and recreate cleanly
    _run_cmd(["sudo", "-n", "nmcli", "con", "delete", HOTSPOT_CON_NAME], timeout=8)

    create_cmd = [
        "sudo", "-n", "nmcli", "con", "add",
        "type", "wifi",
        "ifname", "wlan0",
        "con-name", HOTSPOT_CON_NAME,
        "autoconnect", "no",
        "ssid", HOTSPOT_SSID,
        "802-11-wireless.mode", "ap",
        "802-11-wireless.band", "bg",
        "ipv4.method", "shared",
        "ipv4.addresses", f"{HOTSPOT_IP}/24",
    ]
    add_res = _run_cmd(create_cmd, timeout=10)
    if add_res.returncode != 0:
        logger.error(f"[Wi-Fi] Failed to create hotspot profile: {add_res.stderr}")
        return {"ok": False, "message": f"Failed to create hotspot: {add_res.stderr.strip()}"}

    up_res = _run_cmd(["sudo", "-n", "nmcli", "con", "up", HOTSPOT_CON_NAME], timeout=15)
    if up_res.returncode != 0:
        logger.error(f"[Wi-Fi] Failed to bring up hotspot: {up_res.stderr}")
        return {"ok": False, "message": f"Failed to activate hotspot: {up_res.stderr.strip()}"}

    logger.info(f"[Wi-Fi] Captive Portal Hotspot '{HOTSPOT_SSID}' ACTIVE at http://{HOTSPOT_IP}:{PORT}/wifi-setup")
    return {
        "ok": True,
        "message": f"HOTSPOT '{HOTSPOT_SSID}' ACTIVE! JOIN ON YOUR PHONE.",
        "status": get_wifi_status(),
    }


def stop_setup_hotspot() -> Dict[str, Any]:
    """Stops the `8BIT-JUKEBOX-SETUP` hotspot and lets NetworkManager reconnect to known Wi-Fi."""
    if not is_linux_with_nmcli():
        return {"ok": False, "message": "Not running on Linux."}

    _run_cmd(["sudo", "-n", "nmcli", "con", "down", HOTSPOT_CON_NAME], timeout=10)
    _run_cmd(["sudo", "-n", "nmcli", "con", "delete", HOTSPOT_CON_NAME], timeout=8)
    _run_cmd(["sudo", "-n", "nmcli", "dev", " set", "wlan0", "autoconnect", "yes"], timeout=5)
    time.sleep(2)
    return {"ok": True, "message": "SETUP HOTSPOT STOPPED.", "status": get_wifi_status()}


def connect_to_wifi(ssid: str, password: str = "") -> Dict[str, Any]:
    """
    Brings down the setup hotspot (if active) and connects `wlan0` to `ssid`.
    If connection fails, automatically restores the setup hotspot so the user isn't locked out.
    """
    global _connecting_in_progress
    if not is_linux_with_nmcli():
        return {"ok": False, "message": "Wi-Fi switching is only supported on the Raspberry Pi."}

    ssid = ssid.strip()
    if not ssid:
        return {"ok": False, "message": "SSID is required."}

    _connecting_in_progress = True
    try:
        st_before = get_wifi_status()
        was_hotspot = st_before.get("hotspot_active", False)

        if was_hotspot:
            logger.info(f"[Wi-Fi] Stopping setup hotspot to connect to '{ssid}'...")
            _run_cmd(["sudo", "-n", "nmcli", "con", "down", HOTSPOT_CON_NAME], timeout=10)
            _run_cmd(["sudo", "-n", "nmcli", "con", "delete", HOTSPOT_CON_NAME], timeout=8)
            time.sleep(2)

        _run_cmd(["sudo", "-n", "nmcli", "dev", "wifi", "rescan", "ifname", "wlan0"], timeout=10)
        time.sleep(1)

        cmd = ["sudo", "-n", "nmcli", "--wait", "25", "dev", "wifi", "connect", ssid, "ifname", "wlan0"]
        if password and password.strip():
            cmd.extend(["password", password.strip()])

        logger.info(f"[Wi-Fi] Connecting wlan0 to SSID '{ssid}'...")
        res = _run_cmd(cmd, timeout=30)

        if res.returncode == 0:
            logger.info(f"[Wi-Fi] Successfully connected to '{ssid}'!")
            return {
                "ok": True,
                "message": f"CONNECTED TO '{ssid}'! CHECK TV BIG SCREEN FOR NEW QR CODE.",
                "status": get_wifi_status(),
            }

        err_msg = (res.stderr or res.stdout or "Connection failed").strip()
        logger.warning(f"[Wi-Fi] Failed to connect to '{ssid}': {err_msg}")

        # If we were in hotspot mode and failed to join the target Wi-Fi, bring the hotspot back up!
        if was_hotspot:
            logger.info("[Wi-Fi] Restoring setup hotspot after failed connection attempt...")
            start_setup_hotspot()

        return {
            "ok": False,
            "message": f"COULD NOT CONNECT TO '{ssid}': {err_msg}",
            "status": get_wifi_status(),
        }
    finally:
        _connecting_in_progress = False


async def wifi_watchdog_loop(
    on_state_change: Optional[Callable[[], Coroutine[Any, Any, None]]] = None,
) -> None:
    """
    Background task:
    - Installs captive portal iptables/dnsmasq rules on boot.
    - Pre-scans Wi-Fi networks so a fresh list is cached.
    - If the Pi is completely offline (neither Wi-Fi client nor Ethernet connected, and
      hotspot is not active) for ~30 seconds, automatically starts the `8BIT-JUKEBOX-SETUP`
      captive portal hotspot!
    """
    if not is_linux_with_nmcli():
        return

    await asyncio.to_thread(ensure_captive_portal_system_rules)
    await asyncio.to_thread(scan_wifi_networks, True)

    offline_since: Optional[float] = None
    last_hotspot_state: Optional[bool] = None

    while True:
        try:
            await asyncio.sleep(10)
            if _connecting_in_progress:
                offline_since = None
                continue

            st = await asyncio.to_thread(get_wifi_status)
            is_online = bool(st["connected_ssid"] or st["eth_connected"])
            hotspot_active = bool(st["hotspot_active"])

            if last_hotspot_state is not None and last_hotspot_state != hotspot_active:
                if on_state_change:
                    await on_state_change()
            last_hotspot_state = hotspot_active

            if is_online or hotspot_active:
                offline_since = None
            else:
                if offline_since is None:
                    offline_since = time.time()
                    logger.info("[Wi-Fi Watchdog] No network connection detected; waiting 25s before starting Setup Hotspot...")
                elif time.time() - offline_since >= 25.0:
                    logger.info("[Wi-Fi Watchdog] Still offline after 25s -> Starting Captive Portal Setup Hotspot!")
                    await asyncio.to_thread(start_setup_hotspot)
                    offline_since = None
                    if on_state_change:
                        await on_state_change()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"[Wi-Fi Watchdog] error: {exc}")
