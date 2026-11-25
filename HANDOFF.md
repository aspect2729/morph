# Morph — project handoff (for the next AI agent or developer)

Last updated: 25 Nov 2026. Owner's machine: macOS (Apple Silicon), user `advik`, project at **`~/Morph`**.

Read this whole file before changing anything. It explains what Morph is, how every part works,
what was learned the hard way, the exact tech stack, how to rebuild it from scratch, and what's next.

---

## 1. What Morph is

A local-first **"convert anything + edit any PDF"** app with a minimal, Nothing-phone-inspired UI
(black/white, dot-matrix "Doto" font, one red accent).

Two parts in one web UI:

1. **Convert** — drop any file(s) and convert to any sensible format: Office (Word/Excel/PowerPoint,
   Apple Pages/Numbers/Keynote, ODF) ↔ PDF; PDF → editable Word/Markdown/HTML/TXT/images; images
   (incl. HEIC/AVIF/SVG/PSD) ↔ each other/PDF; Markdown/HTML/EPUB/LaTeX/RST/notebooks ↔ PDF/DOCX/…;
   CSV/JSON/XLSX ↔ each other; audio/video ↔ any format, GIF, thumbnails; **AI**: OCR (Tesseract)
   and speech-to-text/subtitles (Whisper). Batch "convert all to…", "download all" zip, thumbnails.
2. **Edit PDF** (Acrobat-style) — open PDF / image / Office doc; **edit existing text** (keeps the
   PDF's own embedded font), **edit text inside scans/photos** (OCR + glyph cloning written into the
   scan pixels — see §5), add text, highlight, freehand draw, rectangle, whiteout, true redaction,
   image, signature (draw/type), page thumbnails (reorder by drag, rotate, delete, blank page, merge
   more files), undo/redo, zoom, export PDF / Word / PNG / TXT. **Home screen with Recent files**
   (thumbnails, search, remove) and a back button.

Runs three ways:
- **Native macOS app** `/Applications/Morph.app` (Swift/WebKit shell that starts the Python server) — main way the owner uses it.
- **Browser**: `Morph.command` or `uvicorn` → http://127.0.0.1:7788.
- **Public web server** (`MORPH_MODE=web`, Docker) — private per-visitor workspaces, limits, HTTPS. Built and fully tested locally; **not deployed yet** (plan: Oracle Cloud Always Free, see §9).

---

## 2. Tech stack

### System programs (the "engines")
| Program | Version tested (Mac / Docker) | Used for |
|---|---|---|
| **LibreOffice** (headless `soffice`) | 26.8 (Homebrew cask) / 25.x (Debian trixie `*-nogui`) | Office ↔ PDF/ODF/DOC/PPT/XLS, Pages/Keynote/Numbers import |
| **Pandoc** | 3.14 / 3.1.11 | Markdown/HTML/EPUB/LaTeX/RST/Org/ipynb conversions, DOCX→MD/EPUB/TXT. Needs ≥ 2.19 (`--embed-resources`) → that's why Docker uses Debian 13 not 12 |
| **Tesseract OCR** (LSTM) | 5.5.4 / 5.5 | OCR, hOCR with per-character boxes (`-c hocr_char_boxes=1`) |
| **FFmpeg** | 9.0 / 7.x | all audio/video, GIF (palettegen), audio extraction, thumbnails. Homebrew build lacks libvorbis → Ogg falls back to Opus codec |
| **Ghostscript** | 10.08 | PDF compression (`/ebook`) |
| **librsvg** (`rsvg-convert`) | 2.63 | SVG → PNG/PDF |
| **Poppler** | (installed, not essential) | — |
| Fonts | macOS Supplemental (Arial, Times New Roman, Courier New) / Linux **Liberation** + DejaVu (metric-compatible) | resolved by `app/fonts.py` |

### Python (3.12, venv at `~/Morph/.venv`, created with `uv`)
Exact pins in `requirements.lock` (63 packages, `uv pip freeze`). Key ones:

| Package | Version | Role |
|---|---|---|
| fastapi / uvicorn[standard] / python-multipart | 0.165 / 0.54 / 0.0.34 | HTTP server |
| **pymupdf** (+ pymupdf4llm, pymupdf-layout) | 2.1 | PDF render, text extraction (`get_text("dict")`), redaction, font insertion, image replace, PDF→Markdown |
| pdf2docx | 0.5.13 | PDF → editable Word |
| pillow / pillow-heif | 13.0 / 1.8.1 | images incl. HEIC/AVIF |
| pandas / openpyxl / xlrd / odfpy / tabulate | 3.1 / 3.1.5 / 2.0.2 / 1.4.1 / 0.10 | spreadsheets, CSV/JSON/MD/HTML tables |
| **faster-whisper** (+ ctranslate2 4.9.1, av 19, onnxruntime 1.32) | 1.2.1 | speech-to-text (model `small` on Mac, `base` on server) |
| **opencv-python-headless** | 5.1 | scan editing: connected components, inpainting, morphology, resize |
| numpy | 2.5.5 | everything image-math |
| markdown | 3.11 | (minor) |

### Frontend
Plain **HTML + CSS + vanilla JavaScript** (no framework, no build step). Google Fonts: **Doto** (dot-matrix headings), **Space Mono** (labels), **Inter Tight** (body), **Caveat** (typed signatures).

### macOS shell
**Swift 6 / AppKit + WKWebView**, compiled with `swiftc` (no Xcode project). Ad-hoc codesigned.

### Deployment
**Docker** (`python:3.12-slim-trixie`), **Docker Compose**, **Caddy 2.10** (automatic Let's Encrypt HTTPS), target **Oracle Cloud Always Free** (Ampere A1 ARM, 2 OCPU / 12 GB, Ubuntu 24.04) + **DuckDNS** free domain. Locally tested with **Colima** (`brew install colima docker docker-buildx`).

---

## 3. Folder layout

```
~/Morph
├── app/
│   ├── server.py      FastAPI app: all routes, per-visitor workspaces, limits, janitor, security headers (491 lines)
│   ├── config.py      env settings: MORPH_MODE=local|web, data dir, limits, Whisper (45)
│   ├── engine.py      conversion router: file kind → targets → best tool (782)
│   ├── pdfedit.py     PDF editor backend: open anything as PDF, render, text lines, build final PDF (581)
│   ├── scanedit.py    seamless editing of scanned text: OCR model, glyph cloning, pixel write-back (947)
│   ├── library.py     Recent files (per workspace in web mode) (175)
│   └── fonts.py       cross-platform TrueType lookup (macOS + Linux) (44)
├── static/
│   ├── index.html     shell: top bar (Convert / Edit PDF), both views, signature modal
│   ├── app.css        design system (tokens, dark/light), Convert view
│   ├── app.js         icons (inline SVG), routing (#convert / #edit), theme, toasts, health/mode text, native hooks
│   ├── convert.js     Convert view logic
│   ├── editor.css     editor layout + home/Recent
│   └── editor.js      the whole PDF editor (893 lines)
├── macos/
│   ├── MorphApp.swift native app (starts server, WKWebView window, downloads, menus, file panels, alerts)
│   ├── Morph.icns     dot-matrix "M" icon
│   └── build.sh       compiles + bundles + signs + installs to /Applications/Morph.app
├── scripts/smoke_test.py   end-to-end test (no deps). `python3 scripts/smoke_test.py URL [--web]`
├── deploy/
│   ├── Caddyfile, setup-server.sh (Docker, iptables 80/443, swap, unattended-upgrades), push.sh (rsync Mac→server)
├── Dockerfile, .dockerignore, docker-compose.yml, .env.example
├── requirements.txt (loose) / requirements.lock (exact pins — use this)
├── setup.sh           one-time Mac install (brew engines + venv)
├── Morph.command      double-click launcher (browser mode)
├── DEPLOY.md          step-by-step Oracle Cloud guide for the owner
├── README.md
├── .work/             temporary files (auto-deleted after 2 h) — gitignored
└── library/           Recent files (persistent) — gitignored
```
**Note:** `~/Morph` is **not its own git repo yet** (the home folder `/Users/advik` is a repo, ignore that).
First thing to do: `cd ~/Morph && git init && git add -A && git commit -m "Morph"` (`.gitignore` already excludes `.venv/ .work/ library/ macos/build/ .morph.log`).

---

## 4. How the backend works

### Server (`app/server.py`)
- One process, **one uvicorn worker** (state is in memory: `uploads`, `jobs`, `pdfedit.SESSIONS`, scan models). Heavy work in a `ThreadPoolExecutor(config.WORKERS)`.
- Static UI served from `/` (`StaticFiles`), `Cache-Control: no-cache` for UI files.
- **Workspaces:** middleware sets `request.state.ws`. Local mode → `"local"`. Web mode → 32-hex random cookie `morph_ws` (HttpOnly, SameSite=Lax, Secure behind HTTPS). Every route checks ownership via `own_upload / own_job / own_session`. IDs validated by regex.
- **Limits (web):** streamed upload size cap (413), `limit()` per-visitor rate limiter, per-workspace concurrent jobs, page cap, Whisper media-length cap, generic error messages.
- **Janitor thread** (every 5 min): deletes `.work` dirs older than TTL, prunes in-memory dicts, editor sessions, scan models, expired library workspaces.
- **Security headers:** nosniff, no-referrer, X-Frame-Options DENY, Permissions-Policy, strict CSP in web mode (allows Google Fonts, `data:` images). Docs/OpenAPI disabled in web mode.

API (all JSON unless noted):
```
GET  /api/health                         {ok, mode, engines{...}, limits{...}}
POST /api/upload (multipart file)        {id, name, size, kind, targets[{id,ext,label,group}]}
POST /api/convert {id,target}            {job}
GET  /api/jobs/{job}                     {status running|done|error, progress, message, filename, size}
GET  /api/download/{job}                 file
GET  /api/thumb/{upload_id}              jpg
POST /api/zip {jobs:[...]}               {job}
POST /api/pdf/session                    {sid}
POST /api/pdf/{sid}/add?remember=1 (multipart)   {src, name, pages[{w,h,hasText,scanned}], lib}
POST /api/pdf/{sid}/add-from {upload|job|library}  same
GET  /api/pdf/{sid}/render/{src}/{i}?w=  png
GET  /api/pdf/{sid}/lines/{src}/{i}[?ocr=1]   {lines[{id,source pdf|ocr,text,bbox,origin,size,color,bg,family,bold,italic,font,fontxref}], ocr}
POST /api/pdf/{sid}/preview {src,index,edit}  {img (data URL png), rect}   ← scanned-line edit preview
POST /api/pdf/{sid}/save {pages, name, format pdf|docx|png|jpg|txt, lib}  {job, filename, size}
GET  /api/library            DELETE /api/library/{id}            GET /api/library/{id}/thumb
```
Coordinates exchanged with the browser are **PDF points in display space** (after the page's /Rotate).

### Conversion engine (`app/engine.py`)
- `kind_of(name)` by extension → image / vector / pdf / document / slides / spreadsheet / table / data / text / audio / video.
- `targets_for(name)` returns target ids (only those whose engine is installed). Target table `T` maps id → (ext, label, group); groups: Document, Slides, Sheet, Data, Text, Image, Video, Audio, AI, Tools.
- `convert(src, name, target, workdir, progress)` dispatches to `conv_image / conv_svg / conv_pdf / conv_office / conv_table / conv_text / conv_media / ocr / transcribe`.
- LibreOffice is run with a **throwaway profile per call** (`-env:UserInstallation=file:///tmp/...`) under a lock, 240 s timeout, and its `lu*.tmp` junk is deleted. **LibreOffice `txt` export hangs** on some DOCX → DOCX→TXT goes through Pandoc instead.
- FFmpeg progress parsed from `-progress pipe:1`. GIF = two-pass palette (first 30 s, 15 fps, ≤720 px).
- Whisper: audio decoded to 16 kHz WAV, read with `wave` + numpy and passed as an array (avoids a PyAV API mismatch); model loaded once, `vad_filter=True`; outputs transcript / SRT / VTT.
- PDF → MD uses pymupdf4llm; PDF→TXT falls back to OCR if the PDF has no text layer.

### PDF editor backend (`app/pdfedit.py`)
- `add_source` converts anything to PDF (images placed at 150 dpi; Office/text via engine), checks password and page cap, returns page info.
- `lines(..., ocr=False)`: from `page.get_text("dict")` — per line: bbox, baseline origin, dominant span font/size/colour, family/bold/italic guessed from font name+flags, background colour sampled from a render, and the embedded font's **xref** (so edits can reuse the document's own font if it has all glyphs).
- `lines(..., ocr=True)`: builds a **scan PageModel** (§5) and reports its segments.
- `build(spec)` assembles the output: pages from sources (or blank), **scanned-line edits go to scanedit first** (pixels), then `_apply`:
  - PDF text edits → redaction annotation over the old line (`images=NONE`, keeps graphics) → re-insert text **word by word** (so spaces stay real spaces) with: original embedded font if it covers all chars → else system TTF (Arial/Times/Courier or Liberation) → else base-14.
  - added text (wrapped), highlight (real highlight annotation), ink (ink annotation), rectangle, whiteout, image (signatures are PNGs), **redact** (true redaction incl. image pixels).
  - `_fix_tounicode` patches the ToUnicode CMap of inserted fonts (Arial maps space→U+00A0 and hyphen→U+00AD; that broke copy/search).
  - page rotation applied last.

---

## 5. Scanned-text editing (`app/scanedit.py`) — the hardest, most important part

Goal (owner's requirement): editing text in a scan must look like the original — **same font, no box, no new layer**.

Pipeline:
1. **OCR**: page rendered at 300 dpi → Tesseract **hOCR with char boxes**, two passes in parallel: `--psm 3` (layout) + `--psm 11` (sparse; catches isolated table cells). Sparse words that don't overlap pass-1 words are added.
2. **Segments**: OCR lines split at big horizontal gaps (table columns) or vertical jumps → each segment is one editable line.
3. **Canvas**: if the page has one big upright image (typical scan), edit **that image at native resolution** (`Canvas.kind="image"`), else a 300 dpi page raster (`"raster"`, written back as a patch).
4. **Glyphs**: per word, threshold ink, connected components; if component count == char count → exact glyphs (`clean=True`), else split at the least-ink columns near Tesseract's char centres (`_cut_impl`). Each glyph keeps its box, paper/ink levels, RGB ink.
5. **Baseline**: fitted from bottoms of letters that sit on the line; outliers judged against **neighbouring** letters; evaluated **locally** (weighted fit of nearest 7) so it follows tilt **and paper curl**.
6. **Line style**: stroke width (median run length / cap height), **font family** by correlating glyphs with rendered Arial vs Times (sans/serif), page-majority smoothing for uncertain lines, **bold** = stroke > 1.28× page median for that family. Cap height from cap-like glyphs.
7. **Editing a line** (`PageModel.edit`): common **prefix kept untouched**, changed **middle typeset in order**, common **suffix slid as one pixel block** (`_move_block`) only if its position must change; same-length changes (dates) put each new char in its old slot.
8. **Glyph source** (`_source`): candidates = same char anywhere on page with similar cap height **and plausible height for that letter** (zero vs "o"); scored by size, stroke, family/bold, distance, cleanliness; **shape consensus** (medoid of 16×16 normalised shapes) rejects OCR mislabels/broken cuts; lone samples must match the reference font shape. If nothing good → render the char in the **detected font**, blurred to match the scan.
9. **Erase** old glyphs: mask = their boxes, **never other glyphs on any nearby line**; paper rebuilt by **TELEA inpainting from paper-only pixels** (all ink near the hole masked) + matched grain noise. (Morphological closing alone brightened paper — fixed.)
10. **Draw**: glyph alpha from its source pixels, scaled, composited with the line's ink colour (never lightens paper). Letters from other lines snap to the baseline by class (descenders hang, dashes float).
11. **Write back**: `replace_image` on the output page's matching image (JPEG q94 4:4:4), or insert a PNG patch. `preview()` does the same on the cached model and returns the changed region; then `revert()`.

Models are cached per `(sid, src, page)` (`scanedit.put/get/drop_session`).
Known limits: new text is straight on tilted photos (slightly crisper); handwriting isn't editable; changing font/size/colour of a scanned line falls back to rendered font text.

Manual quality testing that was used: render original vs edited crops side by side (a small script that builds a `PageModel`, calls `m.edit(line, new_text)` and saves `numpy` crops) on the owner's real scanned attendance PDF (tilted phone scan, Times letter page + sans table pages).

---

## 6. Frontend notes

- `index.html` loads `app.js` → `convert.js` → `editor.js`; views registered in `VIEWS` and switched by hash.
- **Convert** (`convert.js`): XHR upload with progress, chips grouped by type, polling `/api/jobs`, dot-matrix progress bar, thumbnails, Edit button → `VIEWS.edit.openFrom({upload|job})`.
- **Editor** (`editor.js`) state `S`: `pages[{key, src, index, w, h, rotate, blank, edits{lineId→{line,text,size,color,family,bold,italic,preview}}, objects[{type,rect|paths,...}], lines, lstate}]`, `zoom`, `tool`, `sel`, `active`, `undo/redo` (JSON snapshots), `lib` (Recent id), `sid`.
  - Layers per page: `<img>` render, `.lines` (editable `.tl` divs, contenteditable=plaintext-only), `.objs` (added objects), `.veil` (OCR scanning animation). Pointer events on `.objs` only for real objects (an earlier bug blocked line clicks).
  - Scanned lines: after Enter, `requestPreview` → shows the engine's pixel patch `<img class="tlp">` so what you see is what gets saved.
  - Tools/keys: V select, E edit text, T add text, H highlight, D draw, B rectangle, W whiteout, R redact, I image, S sign; ⌘Z/⇧⌘Z, ⌘S, ⌘+/−/0, Delete, arrows nudge, ⌘A selects within the edited line.
  - Home screen `#edhome` (Recent grid from `/api/library`), back button `#edback` (confirms if unsaved).
- `window.morphOpen()` / `window.morphKey('undo'|'redo'|'save')` are called by the native app's menus.
- Web mode (health `mode==="web"`): status "Private workspace", privacy footer, per-file size stat.

---

## 7. Native macOS app (`macos/MorphApp.swift`)

- Starts `~/Morph/.venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 7788` as a **child Process**, waits for `/api/health`, loads it in a **WKWebView** window; quits the server on ⌘Q. Log: `~/Morph/.morph.log`.
- `Info.plist` key `MorphDirectory` = path of the project (set by `build.sh` from its own location). Bundle id `local.morph.converter`.
- Handles: file picker (`runOpenPanelWith`), downloads → `~/Downloads` (+ reveal in Finder), JS `alert/confirm`, Dock-icon file drops, menus (Open ⌘O, Save ⌘S, Undo/Redo → JS, Reload, Full Screen).
- Build/install: `~/Morph/macos/build.sh` (quit the app first: `osascript -e 'tell application id "local.morph.converter" to quit'`).

Hard-won lessons:
- A **shell-script .app** that spawns the server dies when the launcher exits (macOS kills the app's process tree/launchd jobs) → that's why the app is native Swift and owns the server process.
- launchd jobs **cannot read `~/Downloads`** (TCC privacy) → project moved to `~/Morph`.
- WhatsApp's container folder is unreadable to other apps → users must save files to Downloads first.

---

## 8. Run & develop locally (Mac)

```bash
cd ~/Morph && ./setup.sh                      # first time: brew engines + venv from requirements.txt
.venv/bin/python -m uvicorn app.server:app --port 7788           # dev server (local mode)
MORPH_MODE=web MORPH_DATA=/tmp/morphdata .venv/bin/python -m uvicorn app.server:app --port 7790   # test public mode
python3 scripts/smoke_test.py http://127.0.0.1:7788           # 20 checks
python3 scripts/smoke_test.py http://127.0.0.1:7790 --web     # 27 checks incl. privacy + size limit
./macos/build.sh                                               # rebuild/install the Mac app
```
Status at handoff: local smoke test ✅, web-mode smoke test ✅, **Docker image smoke test ✅ (27/27)**, container healthy, ~500 MB RAM in use, extra container checks ✅ (PPTX→PDF, XLSX→CSV, HEIC→JPG, MP4→MP3, Whisper transcript, scanned edit with Liberation fonts).

### Environment variables (`app/config.py`)
`MORPH_MODE` (local|web) · `MORPH_DATA` (web default `/data`) · `MORPH_MAX_UPLOAD_MB` (100 web) · `MORPH_MAX_PAGES` (300) · `MORPH_TTL_HOURS` (2) · `MORPH_LIBRARY_DAYS` (14) · `MORPH_LIBRARY_MAX` (20) · `MORPH_WORKERS` (2) · `MORPH_JOBS_PER_WORKSPACE` (3) · `MORPH_RATE_PER_MIN` (60) · `MORPH_WHISPER` (on|off) · `MORPH_WHISPER_MODEL` (base web / small Mac) · `MORPH_MAX_MEDIA_MINUTES` (20) · `MORPH_OCR_LANG` (eng; e.g. `eng+hin` needs extra tessdata) · `MORPH_FONT_DIR` · `HF_HOME` (Whisper model cache, `/data/models` in Docker).

---

## 9. Deployment status & next steps

**Decision:** free hosting on **Oracle Cloud Always Free** (2 OCPU / 12 GB ARM; halved from 4/24 in Aug 2026 — still plenty) with Docker Compose + Caddy + DuckDNS. Render/Koyeb free tiers (512 MB) are too small for LibreOffice; Railway (~$10–20/mo) is the paid fallback; Hugging Face Spaces free Docker is uncertain and non-persistent. Supabase is **optional later** (sign-in + cross-device Recent); it can't run the engines.

Owner's next steps (full guide in `DEPLOY.md`):
1. Sign up at cloud.oracle.com/free (home region Hyderabad/Mumbai).
2. Create instance: Ubuntu 24.04, **VM.Standard.A1.Flex 2 OCPU / 12 GB**, generate+save SSH key; retry other availability domain on "Out of capacity".
3. Security list ingress TCP **80** and **443** from 0.0.0.0/0.
4. DuckDNS subdomain → server public IP.
5. Mac: `~/Morph/deploy/push.sh ubuntu@IP ~/Downloads/key.key`
6. Server: `cd ~/morph && bash deploy/setup-server.sh` → set `DOMAIN` in `.env` → re-login → `docker compose up -d --build` → `docker compose exec morph python scripts/smoke_test.py http://127.0.0.1:8000 --web` → open `https://DOMAIN`.

Open ideas / backlog:
- `git init` + private GitHub repo; optionally CI that builds the image and runs the smoke test.
- Supabase auth (email/Google) mapping user id → workspace id; store library in Supabase Storage.
- Bold detection on scans has false positives (e.g. short lines); italic not detected.
- Scanned text could be rendered with the line's slope for very tilted photos.
- Handwriting editing; multi-language OCR packs in Docker (`tesseract-ocr-hin` etc.).
- Horizontal scaling would need shared state (Redis/DB) — currently single process by design.

---

## 10. Rebuild from scratch (if the code were lost)

1. **Mac prerequisites:** Homebrew, `brew install ffmpeg pandoc poppler ghostscript tesseract librsvg uv python@3.12` and `brew install --cask libreoffice`.
2. `uv venv -p python3.12 .venv && uv pip install -p .venv fastapi "uvicorn[standard]" python-multipart pillow pillow-heif pymupdf pymupdf4llm pdf2docx pandas openpyxl xlrd odfpy tabulate faster-whisper opencv-python-headless numpy`.
3. Write the backend modules in this order, following §4–§5: `config.py` → `fonts.py` → `engine.py` (kind → targets → converters; LibreOffice per-call profile; Pandoc; PyMuPDF; FFmpeg; Tesseract; Whisper) → `library.py` → `scanedit.py` → `pdfedit.py` → `server.py` (routes in §4, workspace middleware, limits, janitor).
4. Frontend: `index.html` + `app.css`/`app.js` (design tokens, icons, router) → `convert.js` → `editor.css`/`editor.js` (state model in §6).
5. `scripts/smoke_test.py` early, and run it after every change (both modes).
6. Native app: `MorphApp.swift` (§7) + `build.sh` (swiftc -O, Info.plist incl. `MorphDirectory`, `CFBundleDocumentTypes` public.data, `NSAllowsLocalNetworking`, ad-hoc codesign, copy to /Applications).
7. Docker: `Dockerfile` (python:3.12-slim-**trixie**, apt: `libreoffice-{writer,calc,impress,draw}-nogui pandoc tesseract-ocr tesseract-ocr-eng ffmpeg ghostscript librsvg2-bin fonts-liberation fonts-dejavu-core tini curl`, pip `-r requirements.lock`, non-root uid 10001, `/data` volume, healthcheck on `/api/health`, uvicorn 1 worker with `--proxy-headers`) + `docker-compose.yml` (morph + caddy:2.10.2-alpine, volumes) + `deploy/Caddyfile` (request_body 120MB, 15 min proxy timeouts, HSTS).

Design language to keep: black `#000` / off-white `#f4f4f2`, single red `#ff3b30`, dot-grid background, pill buttons, mono uppercase micro-labels, dot-matrix progress bars, generous spacing, no clutter.
