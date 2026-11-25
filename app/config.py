"""Morph settings, read from environment variables.

MORPH_MODE=local  (default) — the Mac app: one user, everything on this machine.
MORPH_MODE=web    — public server: every visitor gets a private workspace, limits apply.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


MODE = os.environ.get("MORPH_MODE", "local").lower()
WEB = MODE == "web"

# where files live (Docker mounts a volume at /data)
DATA = Path(os.environ.get("MORPH_DATA", "/data" if WEB else ROOT)).resolve()
WORK = DATA / ".work"            # uploads, conversions, editing sessions (temporary)
LIBRARY = DATA / "library"       # Recent files for the PDF editor

# limits (generous locally, safe on a public server)
MAX_UPLOAD_MB = _int("MORPH_MAX_UPLOAD_MB", 100 if WEB else 4096)
MAX_PAGES = _int("MORPH_MAX_PAGES", 300 if WEB else 5000)
TTL_HOURS = _int("MORPH_TTL_HOURS", 2)                       # temporary files
LIBRARY_DAYS = _int("MORPH_LIBRARY_DAYS", 14 if WEB else 3650)  # an unused workspace's Recent files
LIBRARY_MAX = _int("MORPH_LIBRARY_MAX", 20 if WEB else 60)
WORKERS = _int("MORPH_WORKERS", 2 if WEB else 3)              # conversions running at once (server-wide)
JOBS_PER_WORKSPACE = _int("MORPH_JOBS_PER_WORKSPACE", 3 if WEB else 50)
RATE_PER_MIN = _int("MORPH_RATE_PER_MIN", 60 if WEB else 0)  # heavy requests per minute per visitor (0 = off)

for d in (WORK, LIBRARY):
    d.mkdir(parents=True, exist_ok=True)
