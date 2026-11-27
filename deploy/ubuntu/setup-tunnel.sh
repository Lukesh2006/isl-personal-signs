#!/usr/bin/env bash
# Connect this server to its OWN Cloudflare Tunnel (created in the Cloudflare dashboard).
#
#   sudo bash setup-tunnel.sh        (then paste the tunnel token when asked)
#
# Fully separate from any other cloudflared on this machine:
#   - its own cloudflared program in /opt/personal-signs-tunnel (pinned, checksum-verified);
#     the machine's existing cloudflared binary is never used or updated
#   - its own service (personal-signs-tunnel) and an EMPTY config file in /etc/personal-signs;
#     the service cannot even see /etc/cloudflared or any home folder
#   - never runs 'cloudflared service install' or 'cloudflared tunnel login'
# Safe to run again with a new token.
set -euo pipefail

CF_VERSION=2026.10.7
declare -A CF_SHA256=(
  [amd64]=1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872
  [arm64]=3df43aedc9ff291d1735b5cb576cb97e0b157488796db2e32b28aecabb445775
)
CF_DIR=/opt/personal-signs-tunnel
CONF_DIR=/etc/personal-signs
UNIT=personal-signs-tunnel
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo: sudo bash $0" >&2
  exit 1
fi
id personal-signs >/dev/null 2>&1 || { echo "Run install.sh first." >&2; exit 1; }

other_services() {
  systemctl list-units --type=service --state=running --no-legend --plain 2>/dev/null \
    | awk '{print $1}' | { grep -v '^personal-signs' || true; } \
    | while read -r unit; do echo "$unit $(systemctl show -p MainPID --value "$unit")"; done | sort
}
others_before="$(other_services)"
report_others() {
  local changed
  changed="$(comm -23 <(printf '%s\n' "$others_before") <(other_services) | awk '{print $1}')"
  echo
  if [ -z "$changed" ]; then
    echo "Other services on this machine: none restarted or stopped (untouched)."
  else
    echo "WARNING - these other services restarted or stopped:" >&2
    printf '  %s\n' $changed >&2
  fi
}
trap report_others EXIT

# --- the token ---------------------------------------------------------------------------
read -rsp "Paste the tunnel token from the Cloudflare dashboard, then press Enter: " token
echo
token="$(printf '%s' "$token" | tr -d '[:space:]')"
echo "Received ${#token} characters (hidden on purpose)."
token="${token##*--token}"   # accept the whole command the dashboard shows, too
token="${token##*install}"
# A tunnel token is the text starting with eyJ; drop anything else around it.
if [[ "$token" =~ (eyJ[A-Za-z0-9+/=_-]{40,}) ]]; then
  token="${BASH_REMATCH[1]}"
fi
if [[ "$token" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  echo "That is the Tunnel ID, not the token. Copy the box with 'service install eyJ...' instead." >&2
  exit 1
fi
if ! [[ "$token" =~ ^eyJ[A-Za-z0-9+/=_-]{40,}$ ]]; then
  echo "That does not look like a tunnel token (it should start with eyJ). Nothing was changed." >&2
  exit 1
fi
# The token is base64 (standard or URL-safe, padding optional) of {"a":..,"t":TUNNEL_ID,"s":..}.
tunnel_id="$(TOKEN="$token" python3 - <<'PY'
import base64, json, os, re
t = os.environ["TOKEN"].replace("-", "+").replace("_", "/")
t += "=" * (-len(t) % 4)
try:
    data = json.loads(base64.b64decode(t))
    tid = str(data.get("t", ""))
    print(tid if re.fullmatch(r"[0-9a-f-]{36}", tid) else "ERR fields: " + ",".join(sorted(data)))
except Exception as exc:
    print("ERR " + type(exc).__name__)
PY
)"
if [ -z "$tunnel_id" ] || [ "${tunnel_id#ERR}" != "$tunnel_id" ]; then
  echo "Could not read the tunnel ID from that token (${tunnel_id:-no output}, ${#token} characters). Nothing was changed." >&2
  exit 1
fi
echo "This token belongs to tunnel ID: $tunnel_id"
echo "Check it matches the NEW tunnel's ID in the Cloudflare dashboard (not any other tunnel)."
read -rp "Type yes to continue: " answer
[ "$answer" = yes ] || { echo "Stopped. Nothing was changed."; exit 1; }

# --- our own cloudflared program ---------------------------------------------------------
case "$(uname -m)" in
  x86_64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *) echo "Unsupported CPU: $(uname -m)" >&2; exit 1 ;;
esac
if [ "$("$CF_DIR/cloudflared" --version 2>/dev/null | awk '{print $3}')" != "$CF_VERSION" ]; then
  echo "==> Downloading this project's own cloudflared $CF_VERSION"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/cloudflared" \
    "https://github.com/cloudflare/cloudflared/releases/download/$CF_VERSION/cloudflared-linux-$arch"
  echo "${CF_SHA256[$arch]}  $tmp/cloudflared" | sha256sum --check --status \
    || { echo "cloudflared download failed its checksum check; nothing installed." >&2; rm -rf "$tmp"; exit 1; }
  install -d -m 755 "$CF_DIR"
  install -m 755 "$tmp/cloudflared" "$CF_DIR/cloudflared"
  rm -rf "$tmp"
  echo "    installed in $CF_DIR (checksum verified)"
fi

# --- settings and service ----------------------------------------------------------------
install -d -o root -g personal-signs -m 750 "$CONF_DIR"
umask 027
printf 'TUNNEL_TOKEN=%s\n' "$token" > "$CONF_DIR/tunnel.env"
printf '# Empty on purpose: this tunnel is managed in the Cloudflare dashboard.\n' > "$CONF_DIR/cloudflared.yml"
chown root:personal-signs "$CONF_DIR/tunnel.env" "$CONF_DIR/cloudflared.yml"
chmod 640 "$CONF_DIR/tunnel.env" "$CONF_DIR/cloudflared.yml"

install -m 644 "$SRC_DIR/$UNIT.service" "/etc/systemd/system/$UNIT.service"
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null
systemctl restart "$UNIT"

started="$(date '+%Y-%m-%d %H:%M:%S')"
echo "Waiting for the tunnel to connect (cloudflared runs connectivity checks first; up to 2 minutes)..."
for _ in $(seq 1 60); do
  if journalctl -u "$UNIT" --since "$started" --no-pager 2>/dev/null | grep -q "Registered tunnel connection"; then
    echo "Tunnel $tunnel_id connected (service: $UNIT)."
    exit 0
  fi
  sleep 2
done
echo "The tunnel did not connect yet. Check: sudo journalctl -u $UNIT -n 30" >&2
exit 1
