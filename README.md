# 8-Bit Jukebox MVP — Setup & Operations Guide

An interactive 8-bit arcade music jukebox designed for a **Raspberry Pi 5** (or macOS) connected to a TV/projector and sound system via HDMI.

## Architecture Overview

- **Backend ([`main.py`](./main.py), [`database.py`](./database.py), [`audio_engine.py`](./audio_engine.py), [`ai_scene_generator.py`](./ai_scene_generator.py))**:
  - **FastAPI + SQLite (`jukebox.db`)**: Persists the song queue, votes (1 vote per `client_id` per track), DJ leaderboard, seed pool, and cached AI scenes across reboots.
  - **Audio Pre-fetcher & EBU R128 Normalizer**: Background worker that pre-downloads the Now Playing track, Top 2 queued tracks, and Next Seed track using `yt-dlp` piped through `ffmpeg` (`silenceremove` + `loudnorm=I=-14:TP=-1.5:LRA=11`) into `/tmp/audio_cache/`, and cleans up old files after playback.
  - **Gemini 2.5 Flash 8-Bit Scene Engine**: Generates custom retro stage titles, RPG battle logs, 4-color neon palettes, and 16x16 pixel-art sprites with animation behaviors (`bounce`, `scroll`, `float`, `rain`, `starfield`, `dancers`) for incoming songs.
  - **Automatic Public HTTPS Tunnel**: Spawns an outbound SSH reverse tunnel (`localhost.run`) on startup and automatically updates the Big Screen QR code so guests can join over 5G/LTE or any Wi-Fi network without router configuration.
- **Frontends**:
  - **[`bigscreen.html`](./bigscreen.html) (`/bigscreen`)**: 1080p TV HUD with a 60fps audio-reactive `<canvas>`, Dual-Deck Web Audio API 6-second equal-power crossfader (`deckA` & `deckB`), Up Next Top 5 queue, Top DJs Leaderboard, RPG log ticker, and dynamic QR code.
  - **[`mobile.html`](./mobile.html) (`/mobile`)**: Retro pocket DJ interface with persistent UUID (`localStorage`), random family-friendly 8-bit DJ tags + Re-Roll button, live voting, **Fast Track** (instant queue from Host seed pool), and **Open Search** (`ytmusicapi` search sent to Host Approval queue). Automatically reconnects and re-syncs on phone wake (`visibilitychange`).
  - **[`host.html`](./host.html) (`/host`)**: PIN-protected (`HOST_PIN`) admin console with deck transport controls (`|<< RESTART`, `<< -15S`, `PLAY / PAUSE`, `+15S >>`), `VETO / 6S CROSSFADE SKIP`, 1-tap Open Search approval/rejection, and YouTube Music playlist ID seed loader.

---

## Deploying to Your Raspberry Pi 5 (Tomorrow's Checklist)

### Prerequisites on the Raspberry Pi 5
1. Flash **Raspberry Pi OS Bookworm (64-bit with Desktop)** onto your microSD card / NVMe drive using **Raspberry Pi Imager**.
2. In Raspberry Pi Imager settings (the gear icon / OS customization), enable **SSH**, set your username/password (e.g. `pi` or `glen`), and configure your Wi-Fi credentials.
3. Boot the Pi 5 plugged into your TV/Projector via HDMI.

---

### Step 1: Copy the Project from Your Mac to the Pi
Open a Terminal on your Mac and run `rsync` to copy the project (including your configured `.env` file with your `GEMINI_API_KEY`, while skipping the Mac-specific `venv` folder):

```bash
rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude 'jukebox.db*' \
  /Users/Glen.Foster/8bit-dj/ pi@raspberrypi.local:~/8bit-dj/
```
*(Replace `pi@raspberrypi.local` with your Pi's username and hostname or IP address, e.g., `glen@192.168.1.50`)*

---

### Step 2: Run the Automated Pi Installer (`setup_pi.sh`)
SSH into your Raspberry Pi 5 and run the setup script:

```bash
ssh pi@raspberrypi.local
cd ~/8bit-dj
chmod +x setup_pi.sh
./setup_pi.sh
```

What `./setup_pi.sh` does automatically:
1. Installs system packages (`ffmpeg`, `mpv`, `chromium-browser`, `python3-venv`, `sqlite3`, `unclutter`).
2. Creates `~/8bit-dj/venv` and installs all Python dependencies from `requirements.txt`.
3. Installs, enables, and starts the `systemd` background service (`jukebox-backend.service`).
4. Configures `~/.config/wayfire.ini` to launch Chromium in full-screen `--kiosk` mode with `--autoplay-policy=no-user-gesture-required` pointing to `http://localhost:8000/bigscreen`, and disables screen blanking (`dpms = false`).

---

### Step 3: Reboot the Pi into Kiosk Mode
Once `setup_pi.sh` completes:

```bash
sudo reboot
```

When the Pi 5 finishes booting:
- The **Big Screen HUD** will open automatically in full-screen kiosk mode on your TV/projector and begin playing audio immediately without needing a mouse click.
- After ~3 seconds, the **QR Code** in the bottom-right corner will automatically switch to the live public `https://*.lhr.life/mobile` URL so anyone can scan it with their phone camera and start voting/requesting tracks.

---

## Day-to-Day Operations & Useful Commands

### Accessing the Interfaces
- **Big Screen HUD (on Pi HDMI)**: `http://localhost:8000/bigscreen`
- **Mobile DJ Controller**: Scan the QR code on the Big Screen (or visit `http://<pi-ip>:8000/mobile`)
- **Host Admin Console**: Use the same public tunnel domain from the QR code with `/host` at the end (e.g., `https://xxxx.lhr.life/host`) or `http://<pi-ip>:8000/host`
  - **Default Host PIN**: `1234` (change `HOST_PIN` in `.env` anytime)

### Managing the Service on the Raspberry Pi
- **View live logs (and see the active public tunnel URL)**:
  ```bash
  sudo journalctl -u jukebox-backend.service -f
  ```
- **Restart the backend (e.g. after editing `.env`)**:
  ```bash
  sudo systemctl restart jukebox-backend.service
  ```
- **Stop the backend**:
  ```bash
  sudo systemctl stop jukebox-backend.service
  ```
- **Reset the database (clear all queue history, votes, and leaderboard)**:
  ```bash
  sudo systemctl stop jukebox-backend.service
  rm -f ~/8bit-dj/jukebox.db*
  sudo systemctl start jukebox-backend.service
  ```

### Running Locally on Your Mac Anytime
If you want to run the Jukebox on your Mac again:
```bash
cd /Users/Glen.Foster/8bit-dj
./venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```
