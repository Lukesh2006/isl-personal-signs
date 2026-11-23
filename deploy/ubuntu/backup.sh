#!/usr/bin/env bash
# Daily backup of this project's accounts (its own PocketBase) and the
# engine's recordings/models. Keeps the last 7 in /var/backups/personal-signs.
# Only this project's services and folders are touched.
set -euo pipefail

PB_NAME=personal-signs-pocketbase
DEST=/var/backups/personal-signs
mkdir -p "$DEST"
chmod 700 "$DEST"
stamp="$(date +%Y%m%d-%H%M%S)"

# PocketBase uses SQLite; a short stop gives a consistent copy of its database.
systemctl stop "$PB_NAME"
trap 'systemctl start "$PB_NAME"' EXIT
tar czf "$DEST/backup-$stamp.tgz" -C /var/lib "$PB_NAME/pb_data" personal-signs/data
systemctl start "$PB_NAME"
trap - EXIT

ls -1t "$DEST"/backup-*.tgz | tail -n +8 | xargs -r rm -f
echo "Saved $DEST/backup-$stamp.tgz"
