#!/usr/bin/env bash
# =============================================================================
# 8-Bit Jukebox MVP - Raspberry Pi 5 Automated Provisioning Script
# Target OS: Raspberry Pi OS Bookworm (64-bit, Wayland / Wayfire)
# =============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-$(whoami)}"
USER_HOME="$(eval echo "~${APP_USER}")"
VENV_DIR="${APP_DIR}/venv"
AUDIO_CACHE_DIR="/tmp/audio_cache"
SERVICE_NAME="jukebox-backend.service"
PORT="${PORT:-8000}"

echo "============================================================"
echo "  8-BIT JUKEBOX MVP // RASPBERRY PI 5 BOOKWORM INSTALLER"
echo "  App Directory : ${APP_DIR}"
echo "  Target User   : ${APP_USER}"
echo "============================================================"

# 1. Install System Dependencies (ffmpeg, mpv, python3-venv, chromium)
echo "[1/5] Installing system packages (ffmpeg, mpv, python3-venv, chromium-browser)..."
sudo apt-get update -y
sudo apt-get install -y \
  ffmpeg \
  mpv \
  nodejs \
  python3 \
  python3-venv \
  python3-pip \
  curl \
  sqlite3 \
  unclutter \
  wlrctl \
  wtype \
  iptables \
  dnsmasq-base

# Install chromium-browser (or chromium on Debian Bookworm)
if apt-cache show chromium-browser >/dev/null 2>&1; then
  sudo apt-get install -y chromium-browser
elif apt-cache show chromium >/dev/null 2>&1; then
  sudo apt-get install -y chromium
fi

# Install cloudflared for automatic public QR code tunnel (works over 5G/LTE & any Wi-Fi)
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Installing cloudflared (ARM64) for public HTTPS tunnel..."
  sudo curl -fsSL -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 -o /usr/local/bin/cloudflared
  sudo chmod +x /usr/local/bin/cloudflared
fi

# 2. Prepare Audio Cache & Python Virtual Environment
echo "[2/5] Setting up Python virtual environment & dependencies..."
mkdir -p "${AUDIO_CACHE_DIR}"
chmod 777 "${AUDIO_CACHE_DIR}"

if [ ! -d "${VENV_DIR}" ]; then
  python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/pip" install --upgrade pip wheel
"${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"

# Create .env from .env.example if not present
if [ ! -f "${APP_DIR}/.env" ] && [ -f "${APP_DIR}/.env.example" ]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
  echo "Created default .env file from .env.example."
fi

# 3. Install & Enable systemd Service (jukebox-backend.service)
echo "[3/5] Configuring systemd service: ${SERVICE_NAME}..."
sudo tee "/etc/systemd/system/${SERVICE_NAME}" > /dev/null <<EOF
[Unit]
Description=8-Bit Jukebox MVP FastAPI Backend & Audio Pre-fetcher
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${APP_DIR}/.env
ExecStartPre=/bin/mkdir -p ${AUDIO_CACHE_DIR}
ExecStart=${VENV_DIR}/bin/uvicorn main:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

# 4. Configure Desktop Autologin & Kiosk Auto-Start (labwc + wayfire + XDG autostart)
echo "[4/5] Configuring HDMI Desktop Autologin & Big Screen Kiosk mode..."
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_boot_behaviour B4 || true
fi

WAYFIRE_DIR="${USER_HOME}/.config"
WAYFIRE_INI="${WAYFIRE_DIR}/wayfire.ini"
LABWC_DIR="${USER_HOME}/.config/labwc"
LABWC_AUTOSTART="${LABWC_DIR}/autostart"
XDG_AUTOSTART_DIR="${USER_HOME}/.config/autostart"
mkdir -p "${WAYFIRE_DIR}" "${LABWC_DIR}" "${XDG_AUTOSTART_DIR}"
touch "${WAYFIRE_INI}" "${LABWC_AUTOSTART}"

# Resolve chromium binary name
CHROMIUM_BIN="chromium-browser"
if ! command -v chromium-browser >/dev/null 2>&1 && command -v chromium >/dev/null 2>&1; then
  CHROMIUM_BIN="chromium"
fi

chmod +x "${APP_DIR}/launch_kiosk.sh"
KIOSK_CMD="${APP_DIR}/launch_kiosk.sh"

# Configure labwc autostart (Bookworm/Trixie default compositor)
if ! grep -q "launch_kiosk.sh" "${LABWC_AUTOSTART}" && ! grep -q "http://localhost:${PORT}/bigscreen" "${LABWC_AUTOSTART}"; then
  echo "${KIOSK_CMD} &" >> "${LABWC_AUTOSTART}"
fi
chmod +x "${LABWC_AUTOSTART}"

# Configure Wayfire (older Bookworm compositor)
if ! grep -q "\[autostart\]" "${WAYFIRE_INI}"; then
  cat >> "${WAYFIRE_INI}" <<EOF

[autostart]
jukebox_kiosk = ${KIOSK_CMD}
dpms = false
screensaver = false
EOF
else
  # Update or append jukebox_kiosk under existing [autostart]
  if grep -q "^jukebox_kiosk" "${WAYFIRE_INI}"; then
    sed -i.bak "s|^jukebox_kiosk.*|jukebox_kiosk = ${KIOSK_CMD}|" "${WAYFIRE_INI}"
  else
    sed -i.bak "/^\[autostart\]/a\\
jukebox_kiosk = ${KIOSK_CMD}
" "${WAYFIRE_INI}"
  fi
fi

chown -R "${APP_USER}:${APP_USER}" "${WAYFIRE_DIR}"

# 5. Final Verification
echo "[5/5] Verifying service status..."
sudo systemctl status "${SERVICE_NAME}" --no-pager || true

echo "============================================================"
echo "  INSTALLATION COMPLETE!"
echo "  - Big Screen HUD : http://localhost:${PORT}/bigscreen"
echo "  - Mobile DJ UI   : http://$(hostname -I | awk '{print $1}'):${PORT}/mobile"
echo "  - Host Console   : http://$(hostname -I | awk '{print $1}'):${PORT}/host"
echo "============================================================"
