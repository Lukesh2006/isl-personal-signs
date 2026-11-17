#!/usr/bin/env bash
# Install or update the personal sign recognition server on Ubuntu / Linux Mint.
#
#   sudo bash install.sh
#
# Safe to run again: it updates the code and keeps settings, the API key and
# all recordings. Guide: docs/self-hosting-ubuntu.md
set -euo pipefail

REPO_URL="https://github.com/Lukesh2006/isl-personal-signs.git"
APP_DIR=/opt/personal-signs
DATA_DIR=/var/lib/personal-signs
CONF_DIR=/etc/personal-signs

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo: sudo bash $0" >&2
  exit 1
fi

echo "==> System packages"
apt-get update -qq
apt-get install -y -qq git python3 python3-venv curl openssl libgl1 libglib2.0-0 >/dev/null

echo "==> Service user"
id personal-signs >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin personal-signs

echo "==> Code in $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

echo "==> Python packages"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet "$APP_DIR"

echo "==> MediaPipe model files"
mkdir -p "$DATA_DIR/data" "$DATA_DIR/models"
PERSONAL_SIGNS_MODELS_DIR="$DATA_DIR/models" "$APP_DIR/.venv/bin/python" -m personal_signs download-models >/dev/null
chown -R personal-signs:personal-signs "$DATA_DIR"
chmod 750 "$DATA_DIR"

echo "==> Settings in $CONF_DIR"
mkdir -p "$CONF_DIR"
if [ ! -f "$CONF_DIR/personal-signs.env" ]; then
  key="$(openssl rand -hex 24)"
  sed "s/^PERSONAL_SIGNS_API_KEY=.*/PERSONAL_SIGNS_API_KEY=$key/" \
    "$APP_DIR/deploy/ubuntu/personal-signs.env.example" > "$CONF_DIR/personal-signs.env"
  echo "    Created a new API key."
fi
chown -R root:personal-signs "$CONF_DIR"
chmod 750 "$CONF_DIR"
chmod 640 "$CONF_DIR"/*

echo "==> systemd service"
cp "$APP_DIR/deploy/ubuntu/personal-signs.service" /etc/systemd/system/personal-signs.service
systemctl daemon-reload
systemctl enable personal-signs >/dev/null
systemctl restart personal-signs

for _ in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo
    echo "Running on http://127.0.0.1:8000"
    echo "Show the API key with: sudo grep API_KEY $CONF_DIR/personal-signs.env"
    exit 0
  fi
  sleep 2
done
echo "Not answering yet. Check: sudo journalctl -u personal-signs -n 50" >&2
exit 1
