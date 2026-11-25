# Morph

Drop any file. Get it back in any format. Runs 100% on your Mac, nothing is uploaded.

## Start

Open **Morph** from Applications, Launchpad or Spotlight. It's a native Mac app: it starts the
conversion engine, shows the converter in its own window, saves downloads to `~/Downloads`,
and stops the engine when you quit (⌘Q). You can also drop files onto its Dock icon.

- Rebuild or reinstall the app: `./macos/build.sh`
- First time on a new Mac: run `./setup.sh` once to install the engines.
- Without the app: double-click `Morph.command` to run it in your browser.

## Edit PDF

Switch to **Edit PDF** at the top. Open a PDF, a scanned page, a photo of a document, or a Word or PowerPoint file.

- **Edit text**: click any line and type. Real PDF text keeps its original font. On scanned pages and photos,
  the text is read automatically with Tesseract OCR (two passes, so table cells are found too), and your
  new text is painted on the matching paper and ink colour.
- **Add text, highlight, draw, rectangle, whiteout, redact** (redaction permanently removes what's underneath), **image, signature** (draw or type).
- **Pages**: reorder by dragging thumbnails, rotate, delete, add blank pages, merge more files.
- **Undo/redo** (⌘Z / ⇧⌘Z). **Save** as PDF (⌘S), or export as Word, PNG images or text.

Shortcuts: V select · E edit text · T add text · H highlight · D draw · B rectangle · W whiteout · R redact · I image · S sign.

## What converts to what

| You drop | You can get |
|---|---|
| Word, Pages, ODT, RTF | PDF, DOCX, DOC, ODT, RTF, TXT, HTML, Markdown, EPUB, page images |
| PowerPoint, Keynote, ODP | PDF, PPTX, PPT, ODP, PNG/JPG slides |
| Excel, Numbers, ODS | XLSX, XLS, ODS, PDF, CSV (one per sheet), JSON, Markdown, HTML |
| CSV, TSV, JSON | XLSX, ODS, PDF, JSON, CSV/TSV, Markdown, HTML |
| PDF | Editable Word, TXT, Markdown, HTML, PNG/JPG pages, compressed PDF, searchable PDF (OCR) |
| Images (PNG, JPG, HEIC, WEBP, AVIF, TIFF, GIF, BMP, ICO, PSD) | Any of those, PDF, text via OCR, searchable PDF |
| SVG | PNG, JPG, WEBP, PDF |
| Markdown, HTML, EPUB, LaTeX, RST, Org, Jupyter | PDF, DOCX, ODT, RTF, HTML, Markdown, TXT, EPUB, LaTeX |
| Audio (MP3, WAV, M4A, FLAC, AIFF, OGG, OPUS…) | Any audio format |
| Video (MP4, MOV, MKV, WEBM, AVI…) | Any video format, GIF, MP3/WAV/M4A, thumbnail |

## Engines

LibreOffice (Office files), Pandoc (documents), PyMuPDF and pymupdf4llm (PDF), pdf2docx (PDF to Word),
Ghostscript (compression), Tesseract (OCR), FFmpeg (audio/video), Pillow with HEIF/AVIF (images), pandas (data).

## Settings (environment variables)

- `MORPH_OCR_LANG`: for example `eng+hin` (install more with `brew install tesseract-lang`)
- `MORPH_PORT`: default `7788`

Converted files live in `.work/` and are deleted automatically after 2 hours.

## Put it online

Morph can run as a public website with private per-visitor workspaces, limits and automatic cleanup.
See **DEPLOY.md** for the free Oracle Cloud setup (Docker + automatic HTTPS). Before and after every
deploy, run `python3 scripts/smoke_test.py <url> --web`.

Live at **https://morphpdf.antideploy.app** (Antideploy). Update it with `deploy/antideploy.sh`.

## Desktop app (macOS + Windows)

Download from the website or [GitHub Releases](https://github.com/aspect2729/morph/releases/latest):
`Morph-mac-apple-silicon.dmg` (Apple Silicon, macOS 15+), `Morph-windows-setup.exe` (Windows 10/11).
Everything (LibreOffice, Tesseract, FFmpeg, Pandoc) is inside; it runs fully offline.

- Code: `desktop/morph_desktop.py` (native window via pywebview, local engine on 127.0.0.1)
- Build locally on a Mac: `PYTHON=.venv/bin/python desktop/build_mac.sh` → `dist/Morph-mac-*.dmg`
- Releases: push a tag `vX.Y.Z` — `.github/workflows/desktop.yml` builds all three, smoke-tests each, publishes.
