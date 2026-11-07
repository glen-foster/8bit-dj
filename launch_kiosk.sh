#!/usr/bin/env bash
# Wait for 8-Bit Jukebox FastAPI backend to be ready before opening Chromium Fullscreen
for i in {1..45}; do
  if curl -s http://localhost:8000/api/state >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Ensure PipeWire HDMI audio output is unmuted and set to clean 50% default system volume
if command -v wpctl >/dev/null 2>&1; then
  wpctl set-mute @DEFAULT_AUDIO_SINK@ 0 || true
  wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.50 || true
fi

CHROMIUM_BIN="chromium"
if command -v chromium-browser >/dev/null 2>&1; then
  CHROMIUM_BIN="chromium-browser"
fi

exec "${CHROMIUM_BIN}" --start-fullscreen --password-store=basic --autoplay-policy=no-user-gesture-required --noerrdialogs --disable-infobars --check-for-update-interval=31536000 --app=http://localhost:8000/bigscreen
