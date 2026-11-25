#!/usr/bin/env bash
# One-time setup for a fresh Ubuntu 24.04 server (e.g. Oracle Cloud Always Free, Ampere A1).
# Run from the Morph folder on the server:   bash deploy/setup-server.sh
set -euo pipefail

echo "==> Installing Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

echo "==> Opening ports 80 and 443 in the server firewall"
# Oracle's Ubuntu images ship iptables rules that block everything except SSH.
if command -v iptables >/dev/null; then
  for p in 80 443; do
    sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || \
      sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$p" -j ACCEPT
  done
  if command -v netfilter-persistent >/dev/null; then sudo netfilter-persistent save; fi
fi

echo "==> Adding 4 GB of swap (safety net for very large documents)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Automatic security updates"
sudo apt-get update -y && sudo apt-get install -y unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades || true

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created .env — edit it now (set DOMAIN), e.g.:  nano .env"
fi
echo
echo "Done. Log out and back in once (so Docker works without sudo), then run:"
echo "    docker compose up -d --build"
