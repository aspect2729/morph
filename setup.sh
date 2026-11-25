#!/bin/zsh
# One-time install of Morph's conversion engines + Python deps (macOS, Homebrew).
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
brew install ffmpeg pandoc poppler ghostscript tesseract librsvg uv python@3.12
[ -d /Applications/LibreOffice.app ] || brew install --cask libreoffice
uv venv -p python3.12 .venv
uv pip install -p .venv -r requirements.txt
echo "✓ Morph is ready. Double-click Morph.command to start."
