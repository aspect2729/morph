"""Morph server.

Local mode (the Mac app): binds to 127.0.0.1, one user, nothing leaves the machine.
Web mode (MORPH_MODE=web): every visitor gets a private workspace (an unguessable cookie);
every upload, conversion, editing session and Recent file is tied to it and checked on each
request, with size / rate / concurrency limits and automatic cleanup.
"""
from __future__ import annotations

import collections
import hashlib
import re
import secrets
import shutil
import threading
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import config, engine, library, pdfedit, scanedit

ROOT = config.ROOT
WORK = config.WORK
TTL = config.TTL_HOURS * 3600
COOKIE = "morph_ws"
ID = re.compile(r"^[0-9a-f]{12}$")
SID = re.compile(r"^ed[0-9a-f]{10,24}$")

app = FastAPI(title="Morph", docs_url=None if config.WEB else "/docs", redoc_url=None,
              openapi_url=None if config.WEB else "/openapi.json")
pool = ThreadPoolExecutor(max_workers=config.WORKERS)
uploads: dict[str, dict] = {}
jobs: dict[str, dict] = {}
_hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_hits_lock = threading.Lock()


# =============================================================================== workspace

@app.middleware("http")
async def workspace(request: Request, call_next):
    ws, new = "local", False
    if config.WEB:
        ws = request.cookies.get(COOKIE, "")
        if not re.fullmatch(r"[0-9a-f]{32}", ws):
            ws, new = secrets.token_hex(16), True
    request.state.ws = ws
    try:
        resp = await call_next(request)
    except Exception:
        traceback.print_exc()
        resp = JSONResponse({"detail": "Something went wrong on the server."}, status_code=500)
    if new:
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        resp.set_cookie(COOKIE, ws, max_age=config.LIBRARY_DAYS * 86400, httponly=True,
                        samesite="lax", secure=secure, path="/")
    h = resp.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "no-referrer"
    h["X-Frame-Options"] = "DENY"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if config.WEB:
        h["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    if not request.url.path.startswith("/api/"):
        h["Cache-Control"] = "no-cache"
    return resp


def ws_of(request: Request) -> str:
    return request.state.ws


def limit(request: Request, cost: int = 1) -> None:
    """Per-visitor rate limit for heavy requests (public mode)."""
    if not config.RATE_PER_MIN:
        return
    now = time.time()
    with _hits_lock:
        q = _hits[ws_of(request)]
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) + cost > config.RATE_PER_MIN:
            raise HTTPException(429, "You're going a bit fast — please wait a minute and try again.")
        for _ in range(cost):
            q.append(now)


def own_upload(request: Request, uid: str) -> dict:
    up = uploads.get(uid) if ID.match(uid or "") else None
    if not up or up["ws"] != ws_of(request) or not up["path"].exists():
        raise HTTPException(404, "File expired — drop it again")
    return up


def own_job(request: Request, jid: str) -> dict:
    j = jobs.get(jid) if ID.match(jid or "") else None
    if not j or j["ws"] != ws_of(request):
        raise HTTPException(404, "File expired")
    return j


def session_for(request: Request, sid: str) -> str:
    """sid, or a fresh session when the browser sends "new" (saves a round trip when opening a file)."""
    if sid == "new":
        return pdfedit.new_session(WORK, ws_of(request))
    own_session(request, sid)
    return sid


def own_session(request: Request, sid: str) -> None:
    s = pdfedit.SESSIONS.get(sid) if SID.match(sid or "") else None
    if not s or s.get("ws") != ws_of(request) or not s["dir"].exists():
        raise HTTPException(400, "Editing session expired. Open the file again.")


async def save_upload(file: UploadFile, dest: Path) -> int:
    """Stream an upload to disk, refusing anything over the size limit."""
    cap = config.MAX_UPLOAD_MB * 1024 * 1024
    n = 0
    with dest.open("wb") as f:
        while chunk := await file.read(1 << 20):
            n += len(chunk)
            if n > cap:
                break
            f.write(chunk)
    if n > cap:
        dest.unlink(missing_ok=True)
        raise HTTPException(413, f"That file is larger than {config.MAX_UPLOAD_MB} MB.")
    if n == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "That file is empty.")
    return n


def clean_name(name: str | None) -> str:
    name = Path(name or "file").name.replace("\x00", "")
    return name[:180] or "file"


# =============================================================================== janitor

def janitor():
    while True:
        try:
            cutoff = time.time() - TTL
            for d in WORK.iterdir():
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            for k in [k for k, v in list(uploads.items()) if not v["dir"].exists()]:
                uploads.pop(k, None)
            for k in [k for k, v in list(jobs.items()) if v.get("t", 0) < cutoff]:
                jobs.pop(k, None)
            for sid in [s for s, v in list(pdfedit.SESSIONS.items()) if not v["dir"].exists()]:
                pdfedit.SESSIONS.pop(sid, None)
                scanedit.drop_session(sid)
            library.expire_workspaces()
            with _hits_lock:
                for k in [k for k, q in _hits.items() if not q or q[-1] < time.time() - 120]:
                    _hits.pop(k, None)
        except Exception:
            traceback.print_exc()
        time.sleep(300)


threading.Thread(target=janitor, daemon=True).start()
threading.Thread(target=engine.warm_up, daemon=True).start()   # LibreOffice ready before the first visitor


# =============================================================================== convert

@app.get("/api/health")
def health():
    return {"ok": True, "mode": config.MODE, "engines": engine.engines(),
            "limits": {"upload_mb": config.MAX_UPLOAD_MB, "ttl_hours": config.TTL_HOURS,
                       "pages": config.MAX_PAGES,
                       "library_days": config.LIBRARY_DAYS}}


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    limit(request)
    name = clean_name(file.filename)
    uid = uuid.uuid4().hex[:12]
    d = WORK / uid
    d.mkdir()
    src = d / ("source" + Path(name).suffix.lower()[:10])
    try:
        size = await save_upload(file, src)
    except HTTPException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    uploads[uid] = {"name": name, "path": src, "dir": d, "ws": ws_of(request)}
    return {"id": uid, "name": name, "size": size, "kind": engine.kind_of(name) or "unknown",
            "targets": [engine.describe(t) for t in engine.targets_for(name)]}


class ConvertReq(BaseModel):
    id: str
    target: str


@app.post("/api/convert")
def convert(request: Request, req: ConvertReq):
    limit(request)
    up = own_upload(request, req.id)
    ws = ws_of(request)
    running = sum(1 for j in list(jobs.values()) if j["ws"] == ws and j["status"] == "running")
    if running >= config.JOBS_PER_WORKSPACE:
        raise HTTPException(429, "Please wait for your current conversions to finish.")
    if req.target not in engine.targets_for(up["name"]):
        raise HTTPException(400, "That conversion isn't available for this file.")
    jid = uuid.uuid4().hex[:12]
    job = {"status": "running", "progress": None, "message": "Queued", "error": None,
           "output": None, "ws": ws, "t": time.time()}
    jobs[jid] = job

    def prog(p, msg):
        job["progress"] = p
        job["message"] = msg

    def work():
        try:
            out = engine.convert(up["path"], up["name"], req.target, up["dir"] / f"out-{jid}", prog)
            job.update(status="done", output=str(out), progress=1, message="Done",
                       size=out.stat().st_size, filename=out.name)
        except engine.ConvertError as e:
            job.update(status="error", error=str(e))
        except Exception as e:
            traceback.print_exc()
            job.update(status="error", error="Conversion failed." if config.WEB else f"{type(e).__name__}: {e}"[:300])

    pool.submit(work)
    return {"job": jid}


@app.get("/api/jobs/{jid}")
def job_status(request: Request, jid: str):
    j = own_job(request, jid)
    return {k: v for k, v in j.items() if k not in ("output", "ws", "t")}


@app.get("/api/download/{jid}")
def download(request: Request, jid: str):
    j = own_job(request, jid)
    if j["status"] != "done" or not Path(j["output"]).exists():
        raise HTTPException(404, "File expired")
    return FileResponse(j["output"], filename=j["filename"])


@app.get("/api/thumb/{uid}")
def thumb(request: Request, uid: str):
    up = own_upload(request, uid)
    cache = up["dir"] / "thumb.jpg"
    if not cache.exists():
        src, k = up["path"], engine.kind_of(up["name"])
        try:
            if k == "pdf":
                import pymupdf
                pg = pymupdf.open(src)[0]
                pg.get_pixmap(matrix=pymupdf.Matrix(240 / pg.rect.width, 240 / pg.rect.width)).save(str(cache))
            elif k == "image":
                from PIL import Image, ImageOps
                import pillow_heif
                pillow_heif.register_heif_opener()
                im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
                im.thumbnail((360, 360))
                im.save(cache, quality=85)
            elif k == "video" and engine.TOOLS["ffmpeg"]:
                engine.run([engine.TOOLS["ffmpeg"], "-y", "-ss", "1", "-i", str(src), "-frames:v", "1",
                            "-vf", "scale=360:-2", str(cache)], timeout=30)
            elif k == "vector":
                from PIL import Image
                png = cache.with_suffix(".png")
                engine.svg_png(src, png, width=360)
                Image.open(png).convert("RGB").save(cache, quality=85)
        except Exception:
            pass
    if not cache.exists():
        raise HTTPException(404)
    return FileResponse(cache, media_type="image/jpeg")


class ZipReq(BaseModel):
    jobs: list[str]


@app.post("/api/zip")
def zip_jobs(request: Request, req: ZipReq):
    limit(request)
    files = []
    for j in req.jobs[:200]:
        job = jobs.get(j) if ID.match(j or "") else None
        if job and job["ws"] == ws_of(request) and job["status"] == "done" and Path(job["output"]).exists():
            files.append(Path(job["output"]))
    if not files:
        raise HTTPException(404, "Nothing to download")
    jid = uuid.uuid4().hex[:12]
    d = WORK / f"zip-{jid}"
    d.mkdir()
    out = d / "Morph-converted.zip"
    seen: dict[str, int] = {}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            n = f.name
            if n in seen:
                seen[n] += 1
                n = f"{Path(n).stem} ({seen[n]}){Path(n).suffix}"
            else:
                seen[n] = 1
            z.write(f, n)
    jobs[jid] = {"status": "done", "output": str(out), "filename": out.name, "size": out.stat().st_size,
                 "progress": 1, "message": "Done", "error": None, "ws": ws_of(request), "t": time.time()}
    return {"job": jid}


# =============================================================================== PDF editor

def _edit_err(e: Exception):
    if isinstance(e, HTTPException):
        raise e
    if isinstance(e, (pdfedit.EditError, engine.ConvertError)):
        raise HTTPException(400, str(e))
    traceback.print_exc()
    raise HTTPException(500, "Something went wrong while editing." if config.WEB else f"{type(e).__name__}: {e}"[:300])


@app.post("/api/pdf/session")
def pdf_session(request: Request):
    limit(request)
    return {"sid": pdfedit.new_session(WORK, ws_of(request))}


def _remember(request: Request, sid: str, r: dict) -> dict:
    """Register a freshly opened document in this visitor's Recent; returns r with its id."""
    try:
        src = pdfedit.session(sid)["sources"][r["src"]]["path"]
        r["lib"] = library.remember(ws_of(request), Path(src), r["name"] if r["name"].lower().endswith(".pdf")
                                    else Path(r["name"]).stem + ".pdf")
    except Exception:
        traceback.print_exc()
    return r


@app.post("/api/pdf/{sid}/add")
async def pdf_add(request: Request, sid: str, file: UploadFile = File(...), remember: bool = False):
    limit(request)
    sid = session_for(request, sid)
    name = clean_name(file.filename)
    d = pdfedit.session(sid)["dir"]
    tmp = d / ("in-" + uuid.uuid4().hex[:6] + Path(name).suffix.lower()[:10])
    await save_upload(file, tmp)
    try:
        r = await run_in_threadpool(pdfedit.add_source, sid, tmp, name)
        r["sid"] = sid
        return _remember(request, sid, r) if remember else r
    except Exception as e:
        _edit_err(e)


class FromReq(BaseModel):
    upload: str | None = None
    job: str | None = None
    library: str | None = None


@app.post("/api/pdf/{sid}/add-from")
def pdf_add_from(request: Request, sid: str, req: FromReq):
    """Open a Recent file, a file dropped in Convert, or a conversion result in the editor."""
    limit(request)
    sid = session_for(request, sid)
    ws = ws_of(request)
    if req.library:
        try:
            path, name = library.path(ws, req.library)
        except (FileNotFoundError, PermissionError):
            raise HTTPException(404, "That file is no longer in Recent")
        try:
            r = pdfedit.add_source(sid, path, name)
        except Exception as e:
            _edit_err(e)
        library.touch(ws, req.library)
        r["lib"] = req.library
        r["sid"] = sid
        return r
    if req.upload:
        up = own_upload(request, req.upload)
        path, name = up["path"], up["name"]
    elif req.job:
        j = own_job(request, req.job)
        if j["status"] != "done":
            raise HTTPException(404, "File expired — drop it again")
        path, name = Path(j["output"]), j["filename"]
    else:
        raise HTTPException(400, "Nothing to open")
    try:
        return _remember(request, sid, dict(pdfedit.add_source(sid, path, name), sid=sid))
    except Exception as e:
        _edit_err(e)


@app.get("/api/pdf/{sid}/source/{src}")
def pdf_source(request: Request, sid: str, src: str):
    """The opened document as a PDF. The browser keeps it so it can reopen the session by itself
    if the server restarts (redeploy, idle shutdown) while someone is editing."""
    own_session(request, sid)
    s = pdfedit.session(sid)["sources"].get(src)
    if not s:
        raise HTTPException(404)
    return FileResponse(s["path"], media_type="application/pdf", headers={"Cache-Control": "private, no-store"})


@app.get("/api/library")
def library_list(request: Request):
    return {"items": library.listing(ws_of(request))}


@app.get("/api/library/{lid}/thumb")
def library_thumb(request: Request, lid: str):
    t = library.thumb(ws_of(request), lid)
    if t is None:
        raise HTTPException(404)
    return FileResponse(t, media_type="image/jpeg")


@app.delete("/api/library/{lid}")
def library_forget(request: Request, lid: str):
    library.forget(ws_of(request), lid)
    return {"ok": True}


@app.get("/api/pdf/{sid}/render/{src}/{index}")
def pdf_render(request: Request, sid: str, src: str, index: int, w: int = 1200):
    own_session(request, sid)
    try:
        return Response(pdfedit.render(sid, src, index, w), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})
    except Exception as e:
        _edit_err(e)


@app.get("/api/pdf/{sid}/lines/{src}/{index}")
def pdf_lines(request: Request, sid: str, src: str, index: int, ocr: bool = False):
    own_session(request, sid)
    if ocr:
        limit(request)
    try:
        return pdfedit.lines(sid, src, index, ocr)
    except Exception as e:
        _edit_err(e)


class PreviewReq(BaseModel):
    src: str
    index: int
    edit: dict


@app.post("/api/pdf/{sid}/preview")
def pdf_preview(request: Request, sid: str, req: PreviewReq):
    own_session(request, sid)
    try:
        return pdfedit.preview(sid, req.src, req.index, req.edit)
    except Exception as e:
        _edit_err(e)


class SaveReq(BaseModel):
    pages: list[dict]
    name: str = "edited"
    format: str = "pdf"
    lib: str | None = None


@app.post("/api/pdf/{sid}/save")
def pdf_save(request: Request, sid: str, req: SaveReq):
    limit(request, 2)
    own_session(request, sid)
    if req.format not in ("pdf", "docx", "png", "jpg", "txt"):
        raise HTTPException(400, "Unknown format")
    if len(req.pages) > config.MAX_PAGES:
        raise HTTPException(400, f"Too many pages (limit {config.MAX_PAGES}).")
    try:
        d = pdfedit.session(sid)["dir"]
        stem = re.sub(r"[^\w.\- ]+", "_", Path(req.name).stem).strip()[:120] or "edited"
        jid = uuid.uuid4().hex[:12]
        out_dir = d / f"save-{jid}"
        out_dir.mkdir()
        pdf = pdfedit.build(sid, {"pages": req.pages}, out_dir / f"{stem}.pdf")
        if req.lib:
            try:
                library.update(ws_of(request), req.lib, pdf, stem)
            except Exception:
                traceback.print_exc()
        out = pdf
        if req.format != "pdf":
            target = {"docx": "docx", "png": "pdf_png", "jpg": "pdf_jpg", "txt": "txt"}[req.format]
            out = engine.convert(pdf, pdf.name, target, out_dir / "x", lambda *a: None)
        jobs[jid] = {"status": "done", "output": str(out), "filename": out.name, "size": out.stat().st_size,
                     "progress": 1, "message": "Done", "error": None, "ws": ws_of(request), "t": time.time()}
        return {"job": jid, "filename": out.name, "size": out.stat().st_size}
    except Exception as e:
        _edit_err(e)


# ---- desktop app downloads: /download/<mac|mac-intel|windows> -> latest GitHub release file
_REPO = "https://github.com/aspect2729/morph"
_DOWNLOADS = {"mac": "Morph-mac-apple-silicon.dmg", "mac-intel": "Morph-mac-intel.dmg",
              "windows": "Morph-windows-setup.exe"}
_dl_seen: dict[str, tuple[bool, float]] = {}


@app.get("/download/{target}", include_in_schema=False)
def download_app(target: str):
    """Redirect to the newest release file; until one is published, to the releases page (never a 404)."""
    from fastapi.responses import RedirectResponse
    import urllib.request
    name = _DOWNLOADS.get(target)
    if not name:
        raise HTTPException(404)
    url = f"{_REPO}/releases/latest/download/{name}"
    ok, t = _dl_seen.get(name, (False, 0.0))
    if time.time() - t > (600 if ok else 60):          # re-check every 10 min (1 min while missing)
        try:
            r = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "morph-download-check"})
            with urllib.request.urlopen(r, timeout=6) as resp:
                ok = resp.status == 200
        except Exception:
            ok = False
        _dl_seen[name] = (ok, time.time())
    return RedirectResponse(url if ok else f"{_REPO}/releases", status_code=302)


_ASSETS = ("app.css", "editor.css", "app.js", "convert.js", "editor.js")


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    """The page, with every CSS/JS link versioned by the file's content (app.css?v=1a2b3c...).
    A CDN or browser that keeps those files for hours (Cloudflare in front of Antideploy does)
    then still loads the new ones right after each deploy, never old JS next to new HTML."""
    static = ROOT / "static"
    html = (static / "index.html").read_text()
    for name in _ASSETS:
        v = hashlib.sha1((static / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f'"{name}"', f'"{name}?v={v}"')
    return HTMLResponse(html)


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
