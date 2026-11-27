#!/usr/bin/env bash
# Connect this server to its OWN Cloudflare Tunnel (created in the Cloudflare dashboard).
#
#   sudo bash setup-tunnel.sh        (then paste the tunnel token when asked)
#
# It runs as a separate service, personal-signs-tunnel, with an empty config file, so any
# other cloudflared already on this machine (its service, /etc/cloudflared, ~/.cloudflared)
# is never read, changed or restarted. Safe to run again with a new token.
set -euo pipefail

CONF_DIR=/etc/personal-signs
UNIT=personal-signs-tunnel
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo: sudo bash $0" >&2
  exit 1
fi
id personal-signs >/dev/null 2>&1 || { echo "Run install.sh first." >&2; exit 1; }
CLOUDFLARED="$(command -v cloudflared || true)"
if [ -z "$CLOUDFLARED" ]; then
  echo "cloudflared is not installed. Install it first (see docs/self-hosting-ubuntu.md)." >&2
  exit 1
fi

read -rsp "Paste the tunnel token from the Cloudflare dashboard, then press Enter: " token
echo
token="$(printf '%s' "$token" | tr -d '[:space:]')"
# Accept the bare token or the whole command the dashboard shows (take its last word).
token="${token##*--token}"
token="${token##*install}"
if ! [[ "$token" =~ ^[A-Za-z0-9+/=_-]{60,}$ ]]; then
  echo "That does not look like a tunnel token. Nothing was changed." >&2
  exit 1
fi

install -d -o root -g personal-signs -m 750 "$CONF_DIR"
umask 027
printf 'TUNNEL_TOKEN=%s\n' "$token" > "$CONF_DIR/tunnel.env"
printf '# Empty on purpose: this tunnel is managed in the Cloudflare dashboard.\n' > "$CONF_DIR/cloudflared.yml"
chown root:personal-signs "$CONF_DIR/tunnel.env" "$CONF_DIR/cloudflared.yml"
chmod 640 "$CONF_DIR/tunnel.env" "$CONF_DIR/cloudflared.yml"

sed "s#@CLOUDFLARED@#$CLOUDFLARED#g" "$SRC_DIR/$UNIT.service" > "/etc/systemd/system/$UNIT.service"
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null
systemctl restart "$UNIT"

for _ in $(seq 1 20); do
  if journalctl -u "$UNIT" --since "-2 min" --no-pager 2>/dev/null | grep -q "Registered tunnel connection"; then
    echo "Tunnel connected (service: $UNIT). Other cloudflared services were not touched."
    exit 0
  fi
  sleep 2
done
echo "The tunnel did not connect yet. Check: sudo journalctl -u $UNIT -n 30" >&2
exit 1
