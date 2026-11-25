#!/usr/bin/env bash
# Copy Morph from this Mac to the server (first deploy and every update).
#   deploy/push.sh ubuntu@203.0.113.10 ~/Downloads/ssh-key-morph.key
# Then on the server:  cd ~/morph && docker compose up -d --build
set -euo pipefail
HOST="${1:?usage: deploy/push.sh ubuntu@SERVER_IP [path/to/private.key]}"
KEY="${2:-}"
SSH="ssh"; [ -n "$KEY" ] && SSH="ssh -i $KEY"
cd "$(dirname "$0")/.."
rsync -az --delete -e "$SSH" \
  --exclude .git --exclude .venv --exclude .work --exclude library --exclude macos/build --exclude '__pycache__' \
  --exclude .env --exclude '.morph.log' --exclude '.launcher.log' --exclude .DS_Store \
  ./ "$HOST":~/morph/
echo "Copied. Now run on the server:  cd ~/morph && docker compose up -d --build"
