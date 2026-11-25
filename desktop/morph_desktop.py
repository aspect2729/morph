"""Morph desktop app: the Morph engine running privately on this computer, in a native window.

Start-up:
  1. find the engines shipped next to the app (LibreOffice, Tesseract, FFmpeg, Pandoc) and put them first;
  2. start the Morph server on a free local port (127.0.0.1 only, nothing is reachable from outside);
  3. open it in a native window (WebKit on macOS, Edge WebView2 on Windows).

`morph --serve [PORT]` runs only the server (used by the build checks).
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "Morph"


def _base_dirs() -> list[Path]:
    """Folders that may hold the bundled engines, for a frozen app and for a source checkout."""
    exe = Path(sys.executable).resolve().parent
    here = Path(__file__).resolve().parent
    dirs = [exe / "engines", exe.parent / "Resources" / "engines", here.parent / "engines", here / "engines"]
    if getattr(sys, "_MEIPASS", None):
        dirs.insert(0, Path(sys._MEIPASS) / "engines")
    return dirs


def _engines() -> Path | None:
    return next((d for d in _base_dirs() if d.is_dir()), None)


def _data_dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    elif os.name == "nt":
        d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    else:
        d = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hide_console_windows() -> None:
    """Windows: engines (LibreOffice, Tesseract, FFmpeg...) run as console programs; without this every
    conversion would flash a black window."""
    import subprocess
    orig = subprocess.Popen.__init__

    def init(self, *a, **kw):
        kw["creationflags"] = kw.get("creationflags", 0) | 0x08000000      # CREATE_NO_WINDOW
        orig(self, *a, **kw)
    subprocess.Popen.__init__ = init


def setup_environment() -> None:
    """Must run before the Morph modules are imported: they read these variables at import time."""
    if os.name == "nt":
        _hide_console_windows()
    eng = _engines()
    if eng:
        bins = [eng / "bin"]
        os.environ["PATH"] = os.pathsep.join(str(b) for b in bins if b.is_dir()) + os.pathsep + os.environ.get("PATH", "")
        for soffice in (eng / "LibreOffice.app" / "Contents" / "MacOS" / "soffice",
                        eng / "LibreOffice" / "program" / "soffice.exe",
                        eng / "LibreOffice" / "program" / "soffice"):
            if soffice.exists():
                os.environ["MORPH_SOFFICE"] = str(soffice)
                break
        if (eng / "tessdata").is_dir():
            os.environ["TESSDATA_PREFIX"] = str(eng / "tessdata")
        if (eng / "fonts").is_dir():
            os.environ.setdefault("MORPH_FONT_DIR", str(eng / "fonts"))
    os.environ.setdefault("MORPH_MODE", "local")
    os.environ.setdefault("MORPH_DATA", str(_data_dir()))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int) -> threading.Thread:
    import uvicorn
    from app.server import app   # noqa: E402  (after setup_environment)

    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True, name="morph-server")
    t.start()
    return t


def wait_ready(port: int, timeout: float = 60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.15)
    return False


def main() -> None:
    setup_environment()
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":          # headless, for build checks
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 7788
        start_server(port)
        ok = wait_ready(port)
        print(f"MORPH_READY http://127.0.0.1:{port}" if ok else "MORPH_FAILED", flush=True)
        if not ok:
            sys.exit(1)
        while True:
            time.sleep(3600)

    import webview
    port = free_port()
    start_server(port)
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(APP_NAME, html=_SPLASH, width=1320, height=860, min_size=(380, 560),
                                   background_color="#000000")

    def load():
        if wait_ready(port):
            window.load_url(f"http://127.0.0.1:{port}/#convert")
        else:
            window.load_html(_FAILED)

    webview.start(load, private_mode=False, storage_path=str(_data_dir() / "webview"))


_SPLASH = """<html><body style="margin:0;background:#000;color:#f4f4f2;font:13px -apple-system,Segoe UI,sans-serif;
display:grid;place-items:center;height:100vh"><div style="text-align:center"><div style="font:700 28px monospace;
letter-spacing:.2em">MORPH<span style="color:#ff3b30">.</span></div><div style="opacity:.5;margin-top:10px">Starting the
engine…</div></div></body></html>"""
_FAILED = """<html><body style="margin:0;background:#000;color:#f4f4f2;font:14px -apple-system,Segoe UI,sans-serif;
display:grid;place-items:center;height:100vh"><div style="text-align:center;max-width:420px">Morph's engine didn't start.
Quit and open Morph again; if it keeps happening, reinstall it from the website.</div></body></html>"""


if __name__ == "__main__":
    # from a source checkout, make the "app" package importable (a frozen build already has it on sys.path;
    # adding the bundle folder twice breaks OpenCV's loader)
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import multiprocessing
    multiprocessing.freeze_support()
    main()
