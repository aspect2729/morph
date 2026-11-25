#!/bin/zsh
# Double-click to launch Morph. Opens http://127.0.0.1:7788 in your browser.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
PORT=${MORPH_PORT:-7788}
if ! [ -x .venv/bin/python ]; then ./setup.sh || exit 1; fi
( sleep 1.5; open "http://127.0.0.1:$PORT" ) &
exec .venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port $PORT
