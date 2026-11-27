#!/usr/bin/env bash
# Install or update the personal sign recognition server on Ubuntu / Linux Mint:
# the recognition engine, PocketBase (accounts) and daily backups.
#
#   sudo bash install.sh
#
# Safe to run again: it updates the code and keeps settings, accounts and all
# recordings. Guide: docs/self-hosting-ubuntu.md
set -euo pipefail

REPO_URL="https://github.com/Lukesh2006/isl-personal-signs.git"
APP_DIR=/opt/personal-signs
DATA_DIR=/var/lib/personal-signs
CONF_DIR=/etc/personal-signs

# PocketBase is pinned to a tested version and checked against the release's
# official SHA-256 list before it is installed. It is a SEPARATE instance with
# its own names, folder and port, so any other PocketBase already running on
# this machine (another project) is never touched.
PB_VERSION=0.40.6
PB_NAME=personal-signs-pocketbase
PB_DIR=/opt/$PB_NAME
PB_DATA=/var/lib/$PB_NAME
PB_PORT="${PB_PORT:-8092}"
ENGINE_PORT=8000
PB_PUBLIC_URL="${PB_PUBLIC_URL:-https://db.signbridgeco.xyz}"

# Stop instead of fighting over a port another program already uses.
port_owner() { ss -ltnpH "sport = :$1" 2>/dev/null | sed -n 's/.*users:(("\([^"]*\)".*/\1/p' | head -n1; }
check_port() {
  local port="$1" expected="$2" owner
  owner="$(port_owner "$port")"
  if [ -n "$owner" ] && ! systemctl is-active --quiet "$expected"; then
    echo "Port $port is already used by '$owner' (another program). Nothing was changed." >&2
    echo "Pick another port, e.g.: sudo PB_PORT=8092 bash $0" >&2
    exit 1
  fi
}

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo: sudo bash $0" >&2
  exit 1
fi

# Proof that nothing else is touched: remember every OTHER running service and its
# process id now, and report at the end if any of them was restarted or stopped.
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
    echo "WARNING - these other services restarted or stopped while installing:" >&2
    printf '  %s\n' $changed >&2
  fi
}
trap report_others EXIT

echo "==> Checking ports ($ENGINE_PORT engine, $PB_PORT accounts)"
check_port "$ENGINE_PORT" personal-signs
check_port "$PB_PORT" "$PB_NAME"

echo "==> System packages"
apt-get update -qq
# --no-upgrade: install only what is missing; never upgrade packages other programs use.
apt-get install -y -qq --no-upgrade git python3 python3-venv curl unzip openssl iproute2 libgl1 libglib2.0-0 >/dev/null

echo "==> Service users"
id personal-signs >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin personal-signs
id "$PB_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$PB_DATA" --shell /usr/sbin/nologin "$PB_NAME"

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
mkdir -p "$DATA_DIR/data" "$DATA_DIR/models" "$DATA_DIR/team-clips"
PERSONAL_SIGNS_MODELS_DIR="$DATA_DIR/models" "$APP_DIR/.venv/bin/python" -m personal_signs download-models >/dev/null
chown -R personal-signs:personal-signs "$DATA_DIR"
chmod 750 "$DATA_DIR"

echo "==> PocketBase $PB_VERSION (accounts)"
case "$(uname -m)" in
  x86_64) pb_arch=amd64 ;;
  aarch64 | arm64) pb_arch=arm64 ;;
  *) echo "Unsupported CPU: $(uname -m)" >&2; exit 1 ;;
esac
installed="$("$PB_DIR/pocketbase" --version 2>/dev/null | awk '{print $NF}' || true)"
if [ "$installed" != "$PB_VERSION" ]; then
  tmp="$(mktemp -d)"
  zip="pocketbase_${PB_VERSION}_linux_${pb_arch}.zip"
  base="https://github.com/pocketbase/pocketbase/releases/download/v${PB_VERSION}"
  curl -fsSL -o "$tmp/$zip" "$base/$zip"
  curl -fsSL -o "$tmp/checksums.txt" "$base/checksums.txt"
  (cd "$tmp" && grep " $zip\$" checksums.txt | sha256sum --check --status) \
    || { echo "PocketBase download failed its checksum check; not installed." >&2; rm -rf "$tmp"; exit 1; }
  mkdir -p "$PB_DIR"
  unzip -oq "$tmp/$zip" pocketbase -d "$PB_DIR"
  rm -rf "$tmp"
  echo "    installed PocketBase $PB_VERSION (checksum verified)"
fi
mkdir -p "$PB_DATA/pb_data"
chown -R "$PB_NAME:$PB_NAME" "$PB_DATA"
chmod 750 "$PB_DATA"

echo "==> Settings in $CONF_DIR"
mkdir -p "$CONF_DIR"
env_file="$CONF_DIR/personal-signs.env"
[ -f "$env_file" ] || cp "$APP_DIR/deploy/ubuntu/personal-signs.env.example" "$env_file"
# Older installs: add the account settings if they are missing.
grep -q '^POCKETBASE_URL=' "$env_file" || echo "POCKETBASE_URL=http://127.0.0.1:$PB_PORT" >> "$env_file"
sed -i "s#^POCKETBASE_URL=.*#POCKETBASE_URL=http://127.0.0.1:$PB_PORT#" "$env_file"
grep -q '^POCKETBASE_PUBLIC_URL=' "$env_file" || echo "POCKETBASE_PUBLIC_URL=$PB_PUBLIC_URL" >> "$env_file"
grep -q '^PERSONAL_SIGNS_TEAM_CLIPS_DIR=' "$env_file" \
  || echo "PERSONAL_SIGNS_TEAM_CLIPS_DIR=$DATA_DIR/team-clips" >> "$env_file"
chown -R root:personal-signs "$CONF_DIR"
chmod 750 "$CONF_DIR"
chmod 640 "$CONF_DIR"/*

echo "==> systemd services and daily backup"
sed -e "s#@PB_NAME@#$PB_NAME#g" -e "s#@PB_PORT@#$PB_PORT#g" \
  "$APP_DIR/deploy/ubuntu/personal-signs-pocketbase.service" > "/etc/systemd/system/$PB_NAME.service"
for unit in personal-signs.service personal-signs-backup.service personal-signs-backup.timer; do
  cp "$APP_DIR/deploy/ubuntu/$unit" "/etc/systemd/system/$unit"
done
install -m 755 "$APP_DIR/deploy/ubuntu/backup.sh" /usr/local/sbin/personal-signs-backup
systemctl daemon-reload
systemctl enable "$PB_NAME" >/dev/null
systemctl restart "$PB_NAME"
systemctl enable personal-signs >/dev/null
systemctl restart personal-signs
systemctl enable --now personal-signs-backup.timer >/dev/null

ok_pb=no
ok_engine=no
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$PB_PORT/api/health" >/dev/null 2>&1 && ok_pb=yes
  curl -fsS "http://127.0.0.1:$ENGINE_PORT/health" >/dev/null 2>&1 && ok_engine=yes
  [ "$ok_pb" = yes ] && [ "$ok_engine" = yes ] && break
  sleep 2
done

echo
echo "PocketBase for Personal ISL Signs:  $ok_pb   http://127.0.0.1:$PB_PORT"
echo "Recognition engine:                 $ok_engine   http://127.0.0.1:$ENGINE_PORT"
if [ "$ok_pb" != yes ] || [ "$ok_engine" != yes ]; then
  echo "Check: sudo journalctl -u $PB_NAME -n 50   /   sudo journalctl -u personal-signs -n 50" >&2
  exit 1
fi
cat <<EOF

Next:
  1. Create the admin for THIS PocketBase (your own email and a strong password):
       sudo -u $PB_NAME $PB_DIR/pocketbase superuser upsert YOUR_EMAIL 'YOUR_PASSWORD' --dir $PB_DATA/pb_data
  2. Connect this project's own tunnel: sudo bash $APP_DIR/deploy/ubuntu/setup-tunnel.sh
     (docs/self-hosting-ubuntu.md, steps 4-5). Never use 'cloudflared service install'.
EOF
