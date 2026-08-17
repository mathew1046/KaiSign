#!/usr/bin/env sh
set -eu

URL="${KIOSK_URL:-http://127.0.0.1:8000/}"
PROFILE_DIR="${KIOSK_CHROMIUM_PROFILE:-$HOME/.config/chromium-kiosk}"

exec chromium-browser \
  --kiosk "$URL" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --disable-background-networking \
  --disable-background-timer-throttling \
  --disable-component-update \
  --disable-default-apps \
  --disable-features=TranslateUI \
  --disable-sync \
  --autoplay-policy=no-user-gesture-required \
  --metrics-recording-only \
  --password-store=basic \
  --use-mock-keychain
