"""Morph conversion engine.

Routes every (input type -> output format) pair to the strongest local tool:
  LibreOffice  – Word / PowerPoint / Excel / Keynote / Pages / Numbers / ODF
  Pandoc       – Markdown, HTML, EPUB, LaTeX, reStructuredText, notebooks …
  PyMuPDF      – PDF text, PDF -> images, page assembly
  pdf2docx     – PDF -> editable Word
  Ghostscript  – PDF compression
  Tesseract    – OCR (image -> text, scanned PDF -> searchable PDF)
  FFmpeg       – every audio / video format, GIFs, audio extraction
  Pillow       – raster images incl. HEIC / AVIF / ICO
  pandas       – spreadsheet <-> CSV / JSON / Markdown / HTML
Everything runs on this machine; nothing is uploaded anywhere.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

Progress = Callable[[float | None, str], None]


class ConvertError(Exception):
    pass


# --------------------------------------------------------------------------- tools

def _which(*names: str) -> str | None:
    for n in names:
        if os.path.isabs(n) and os.access(n, os.X_OK):
            return n
        p = shutil.which(n) or (shutil.which(n, path="/opt/homebrew/bin:/usr/local/bin"))
        if p:
            return p
    return None


TOOLS = {
    "soffice": _which(*filter(None, [os.environ.get("MORPH_SOFFICE")]),
                      "/Applications/LibreOffice.app/Contents/MacOS/soffice", "soffice", "libreoffice"),
    "pandoc": _which("pandoc"),
    "ffmpeg": _which("ffmpeg"),
    "ffprobe": _which("ffprobe"),
    "gs": _which("gs"),
    "tesseract": _which("tesseract"),
    "rsvg": _which("rsvg-convert"),
}


_ENGINES: dict[str, bool] | None = None


def _cfg():
    from . import config
    return config


def engines() -> dict[str, bool]:
    global _ENGINES
    if _ENGINES is not None:
        return _ENGINES

    def has_mod(m):
        import importlib.util
        return importlib.util.find_spec(m) is not None

    _ENGINES = {
        "libreoffice": bool(TOOLS["soffice"]),
        "pandoc": bool(TOOLS["pandoc"]),
        "ffmpeg": bool(TOOLS["ffmpeg"]),
        **({"ghostscript": True} if TOOLS["gs"] else {}),
        "tesseract": bool(TOOLS["tesseract"]),
        **({"rsvg": True} if TOOLS["rsvg"] else {}),
    }
    return _ENGINES


def run(cmd: list[str], timeout: int = 900, cwd: str | None = None) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise ConvertError(f"{Path(cmd[0]).name} timed out")
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-4:]
        raise ConvertError(f"{Path(cmd[0]).name} failed: " + " ".join(tail)[:400])
    return r.stdout


# --------------------------------------------------------------------------- types

IMAGE = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif", "heic", "heif",
         "ico", "avif", "tga", "ppm", "psd", "jfif"}
VECTOR = {"svg"}
PDF = {"pdf"}
DOC = {"doc", "docx", "odt", "rtf", "pages", "wpd", "dotx", "docm"}
SLIDES = {"ppt", "pptx", "odp", "key", "pps", "ppsx", "pptm"}
SHEET = {"xls", "xlsx", "xlsm", "ods", "numbers"}
TABLE = {"csv", "tsv"}
DATA = {"json"}
MARKUP = {"md", "markdown", "html", "htm", "txt", "rst", "tex", "epub", "org",
          "ipynb", "textile", "adoc"}
AUDIO = {"mp3", "wav", "m4a", "aac", "flac", "ogg", "oga", "opus", "aiff", "aif",
         "wma", "amr", "caf"}
VIDEO = {"mp4", "mov", "mkv", "webm", "avi", "m4v", "flv", "wmv", "3gp", "mpg",
         "mpeg", "ts", "mts"}

KINDS = [("image", IMAGE), ("vector", VECTOR), ("pdf", PDF), ("document", DOC),
         ("slides", SLIDES), ("spreadsheet", SHEET), ("table", TABLE), ("data", DATA),
         ("text", MARKUP), ("audio", AUDIO), ("video", VIDEO)]

# human labels for targets.  key -> (extension, label, group)
T = {
    "png": ("png", "PNG", "Image"), "jpg": ("jpg", "JPG", "Image"),
    "webp": ("webp", "WEBP", "Image"), "gif": ("gif", "GIF", "Image"),
    "bmp": ("bmp", "BMP", "Image"), "tiff": ("tiff", "TIFF", "Image"),
    "ico": ("ico", "ICO", "Image"), "avif": ("avif", "AVIF", "Image"),
    "heic": ("heic", "HEIC", "Image"),
    "pdf": ("pdf", "PDF", "Document"), "docx": ("docx", "DOCX", "Document"),
    "odt": ("odt", "ODT", "Document"), "rtf": ("rtf", "RTF", "Document"),
    "doc": ("doc", "DOC", "Document"),
    "txt": ("txt", "TXT", "Text"), "md": ("md", "Markdown", "Text"),
    "html": ("html", "HTML", "Text"), "epub": ("epub", "EPUB", "Text"),
    "tex": ("tex", "LaTeX", "Text"),
    "pptx": ("pptx", "PPTX", "Slides"), "odp": ("odp", "ODP", "Slides"),
    "ppt": ("ppt", "PPT", "Slides"),
    "xlsx": ("xlsx", "XLSX", "Sheet"), "xls": ("xls", "XLS", "Sheet"),
    "ods": ("ods", "ODS", "Sheet"), "csv": ("csv", "CSV", "Sheet"),
    "tsv": ("tsv", "TSV", "Sheet"), "json": ("json", "JSON", "Data"),
    "mp3": ("mp3", "MP3", "Audio"), "wav": ("wav", "WAV", "Audio"),
    "m4a": ("m4a", "M4A", "Audio"), "flac": ("flac", "FLAC", "Audio"),
    "ogg": ("ogg", "OGG", "Audio"), "opus": ("opus", "OPUS", "Audio"),
    "aac": ("aac", "AAC", "Audio"), "aiff": ("aiff", "AIFF", "Audio"),
    "mp4": ("mp4", "MP4", "Video"), "mov": ("mov", "MOV", "Video"),
    "webm": ("webm", "WEBM", "Video"), "mkv": ("mkv", "MKV", "Video"),
    "avi": ("avi", "AVI", "Video"), "vgif": ("gif", "GIF", "Video"),
    "pdf_ocr": ("pdf", "Searchable PDF", "AI"), "ocr_txt": ("txt", "Text via OCR", "AI"),
    "pdf_small": ("pdf", "Compressed PDF", "Tools"),
    "pdf_png": ("png", "PNG pages", "Image"), "pdf_jpg": ("jpg", "JPG pages", "Image"),
    "thumb": ("jpg", "Thumbnail", "Image"),
}


def kind_of(name: str) -> str | None:
    ext = Path(name).suffix.lower().lstrip(".")
    for k, s in KINDS:
        if ext in s:
            return k
    return None


def _pillow_can(fmt: str) -> bool:
    from PIL import features
    try:
        if fmt == "avif":
            return bool(features.check("avif"))
    except Exception:
        return False
    return True


def targets_for(name: str) -> list[str]:
    ext = Path(name).suffix.lower().lstrip(".")
    ext = {"jpeg": "jpg", "tif": "tiff", "htm": "html", "markdown": "md",
           "aif": "aiff", "oga": "ogg", "jfif": "jpg"}.get(ext, ext)
    k = kind_of(name)
    e = engines()
    out: list[str] = []
    if k == "image":
        out = ["png", "jpg", "webp", "gif", "bmp", "tiff", "ico", "heic", "pdf"]
        if _pillow_can("avif"):
            out.insert(3, "avif")
        if e["tesseract"]:
            out += ["ocr_txt", "pdf_ocr"]
    elif k == "vector":
        out = ["png", "jpg", "webp", "pdf"]      # librsvg if installed, else PyMuPDF
    elif k == "pdf":
        out = ["docx", "txt", "md", "html", "pdf_png", "pdf_jpg", "pdf_small"]
        if e["tesseract"]:
            out += ["pdf_ocr", "ocr_txt"]
    elif k == "document":
        if e["libreoffice"]:
            out = ["pdf", "docx", "odt", "rtf", "doc", "txt", "html"]
            if e["pandoc"]:
                out += ["md", "epub"]
            out += ["pdf_png"]
    elif k == "slides":
        if e["libreoffice"]:
            out = ["pdf", "pptx", "odp", "ppt", "pdf_png", "pdf_jpg"]
    elif k == "spreadsheet":
        out = ["csv", "json", "md", "html"]
        if e["libreoffice"]:
            out = ["xlsx", "pdf", "ods", "xls"] + out
    elif k == "table":
        out = ["xlsx", "json", "md", "html", "tsv" if ext == "csv" else "csv"]
        if e["libreoffice"]:
            out += ["pdf", "ods"]
    elif k == "data":
        out = ["csv", "xlsx", "md", "html"]
    elif k == "text":
        if e["pandoc"] or ext == "txt":
            out = ["pdf", "docx", "html", "md", "txt", "epub", "odt", "rtf", "tex"]
            if not e["pandoc"]:
                out = ["pdf", "docx", "odt", "rtf", "html"]
            if not e["libreoffice"]:
                out = [x for x in out if x not in ("pdf", "rtf")]
    elif k == "audio":
        out = ["mp3", "wav", "m4a", "flac", "ogg", "opus", "aac", "aiff"]
    elif k == "video":
        out = ["mp4", "mov", "webm", "mkv", "avi", "vgif", "mp3", "wav", "m4a", "thumb"]
    # never offer converting a file to its own format (except tool/AI ops)
    return [t for t in out if not (T[t][0] == ext and T[t][2] not in ("AI", "Tools")
                                   and t not in ("pdf_png", "pdf_jpg", "thumb"))]


def describe(t: str) -> dict:
    ext, label, group = T[t]
    return {"id": t, "ext": ext, "label": label, "group": group}


# --------------------------------------------------------------------------- helpers

_soffice_lock = threading.Lock()
# One reused LibreOffice profile: creating a fresh one costs ~1-5 s on every conversion.
_LO_PROFILE = Path(tempfile.gettempdir()) / "morph-lo-profile"


def warm_up() -> None:
    """Build the LibreOffice profile and pull its libraries into memory at startup, so the first
    visitor's Office file opens as fast as later ones. Best effort; runs in a background thread."""
    if not TOOLS["soffice"]:
        return
    try:
        d = Path(tempfile.mkdtemp(prefix="morph-warm-"))
        (d / "warm.txt").write_text("Morph\n")
        soffice(d / "warm.txt", "pdf", d / "out")
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def soffice(src: Path, fmt: str, outdir: Path) -> Path:
    """fmt e.g. 'pdf', 'docx', 'xlsx', 'csv:Text - txt - csv (StarCalc):44,34,76'."""
    if not TOOLS["soffice"]:
        raise ConvertError("LibreOffice is not installed")
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = lambda: [TOOLS["soffice"], f"-env:UserInstallation={_LO_PROFILE.as_uri()}", "--headless",
                   "--norestore", "--convert-to", fmt, "--outdir", str(outdir), str(src)]
    try:
        with _soffice_lock:          # one LibreOffice at a time, so they can share one profile
            try:
                run(cmd(), timeout=240)
            except ConvertError:
                # a damaged profile (e.g. after a crash) makes every later call fail: rebuild it once
                shutil.rmtree(_LO_PROFILE, ignore_errors=True)
                run(cmd(), timeout=240)
    finally:
        for junk in outdir.glob("lu*.tmp"):
            junk.unlink(missing_ok=True)
    ext = fmt.split(":")[0]
    out = outdir / f"{src.stem}.{ext}"
    if not out.exists():
        raise ConvertError("LibreOffice could not convert this file")
    return out


def pandoc(src: Path, dst: Path, frm: str | None = None, extra: list[str] | None = None) -> Path:
    if not TOOLS["pandoc"]:
        raise ConvertError("Pandoc is not installed")
    cmd = [TOOLS["pandoc"], str(src), "-o", str(dst), "--standalone"]
    if frm:
        cmd += ["-f", frm]
    if dst.suffix in (".html",):
        cmd += ["--embed-resources", "--metadata", f"title={src.stem}"]
    cmd += extra or []
    run(cmd, cwd=str(src.parent))
    return dst


PANDOC_IN = {"md": "markdown", "markdown": "markdown", "html": "html", "htm": "html",
             "rst": "rst", "tex": "latex", "epub": "epub", "org": "org", "ipynb": "ipynb",
             "textile": "textile", "txt": "markdown", "docx": "docx", "odt": "odt",
             "adoc": "asciidoc"}


def zip_files(files: list[Path], dst: Path) -> Path:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.name)
    return dst


def pdf_to_images(pdf: Path, fmt: str, work: Path, stem: str, progress: Progress) -> Path:
    import pymupdf
    doc = pymupdf.open(pdf)
    n = doc.page_count
    files = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=200, alpha=False)
        f = work / f"{stem}-{i + 1:03d}.{fmt}"
        if fmt == "jpg":
            pix.save(str(f), jpg_quality=92)
        else:
            pix.save(str(f))
        files.append(f)
        progress((i + 1) / n, f"Rendering page {i + 1} of {n}")
    if len(files) == 1:
        return files[0]
    return zip_files(files, work / f"{stem}-{fmt}-pages.zip")


# --------------------------------------------------------------------------- converters

def conv_image(src: Path, t: str, work: Path, stem: str, progress: Progress) -> Path:
    from PIL import Image, ImageOps
    import pillow_heif
    pillow_heif.register_heif_opener()
    ext = T[t][0]
    dst = work / f"{stem}.{ext}"
    progress(None, "Decoding image")
    im = Image.open(src)
    frames_ok = ext in ("gif", "webp", "tiff", "pdf") and getattr(im, "n_frames", 1) > 1
    if not frames_ok:
        im = ImageOps.exif_transpose(im)
    if ext in ("jpg", "bmp", "pdf", "heic") and im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        rgba = im.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    progress(0.6, "Encoding")
    if ext == "jpg":
        im.save(dst, "JPEG", quality=92, optimize=True, progressive=True)
    elif ext == "webp":
        im.save(dst, "WEBP", quality=90, method=6, save_all=frames_ok)
    elif ext == "avif":
        im.save(dst, "AVIF", quality=80)
    elif ext == "heic":
        im.save(dst, "HEIF", quality=90)
    elif ext == "ico":
        im = im.convert("RGBA")
        side = max(im.size)
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        sizes = [s for s in (16, 24, 32, 48, 64, 128, 256) if s <= max(side, 16)]
        sq.save(dst, "ICO", sizes=[(s, s) for s in sizes])
    elif ext == "pdf":
        im.save(dst, "PDF", resolution=150, save_all=frames_ok)
    elif ext == "gif":
        im.save(dst, "GIF", save_all=frames_ok, optimize=True)
    elif ext == "tiff":
        im.save(dst, "TIFF", compression="tiff_lzw", save_all=frames_ok)
    else:
        im.save(dst, ext.upper() if ext != "jpg" else "JPEG")
    return dst


def svg_pdf(src: Path, dst: Path) -> Path:
    """SVG -> PDF with librsvg when installed, else PyMuPDF (the desktop app ships without librsvg)."""
    if TOOLS["rsvg"]:
        run([TOOLS["rsvg"], "-f", "pdf", "-o", str(dst), str(src)])
    else:
        import pymupdf
        with pymupdf.open(str(src), filetype="svg") as d:
            pymupdf.open("pdf", d.convert_to_pdf()).save(dst)
    return dst


def svg_png(src: Path, dst: Path, zoom: float = 2, width: int | None = None) -> Path:
    if TOOLS["rsvg"]:
        run([TOOLS["rsvg"], "-f", "png", *(["-w", str(width)] if width else ["-z", str(zoom)]), "-o", str(dst), str(src)])
    else:
        import pymupdf
        with pymupdf.open(str(src), filetype="svg") as d:
            pg = d[0]
            z = width / pg.rect.width if width else zoom
            pg.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=True).save(str(dst))
    return dst


def conv_svg(src: Path, t: str, work: Path, stem: str, progress: Progress) -> Path:
    ext = T[t][0]
    if not TOOLS["rsvg"]:
        if ext == "pdf":
            return svg_pdf(src, work / f"{stem}.pdf")
        png = svg_png(src, work / f"{stem}.__r.png")
        if ext == "png":
            final = work / f"{stem}.png"
            png.rename(final)
            return final
        return conv_image(png, t, work, stem, progress)
    if ext == "pdf":
        dst = work / f"{stem}.pdf"
        run([TOOLS["rsvg"], "-f", "pdf", "-o", str(dst), str(src)])
        return dst
    png = work / f"{stem}.__r.png"
    run([TOOLS["rsvg"], "-f", "png", "-z", "2", "-o", str(png), str(src)])
    if ext == "png":
        final = work / f"{stem}.png"
        png.rename(final)
        return final
    return conv_image(png, t, work, stem, progress)


def ocr(src: Path, t: str, work: Path, stem: str, progress: Progress) -> Path:
    """OCR images or PDFs with Tesseract (LSTM neural model)."""
    import pymupdf
    pages: list[Path] = []
    if src.suffix.lower() == ".pdf":
        doc = pymupdf.open(src)
        for i, p in enumerate(doc):
            f = work / f"__ocr{i:04d}.png"
            p.get_pixmap(dpi=300).save(str(f))
            pages.append(f)
    else:
        from PIL import Image, ImageOps
        import pillow_heif
        pillow_heif.register_heif_opener()
        f = work / "__ocr0000.png"
        ImageOps.exif_transpose(Image.open(src)).convert("RGB").save(f)
        pages.append(f)
    langs = os.environ.get("MORPH_OCR_LANG", "eng")
    outs = []
    for i, f in enumerate(pages):
        progress(i / len(pages), f"Reading page {i + 1} of {len(pages)}")
        base = f.with_suffix("")
        mode = "txt" if t == "ocr_txt" else "pdf"
        run([TOOLS["tesseract"], str(f), str(base), "-l", langs, "--psm", "3", mode])
        outs.append(base.with_suffix("." + mode))
    if t == "ocr_txt":
        dst = work / f"{stem}.txt"
        dst.write_text("\n\f\n".join(o.read_text(errors="ignore").strip() for o in outs))
        return dst
    dst = work / f"{stem}-searchable.pdf"
    merged = pymupdf.open()
    for o in outs:
        merged.insert_pdf(pymupdf.open(o))
    merged.save(dst, garbage=4, deflate=True)   # 4 = also merge identical fonts/images across pages
    return dst


def conv_pdf(src: Path, t: str, work: Path, stem: str, progress: Progress) -> Path:
    import pymupdf
    if t in ("pdf_ocr", "ocr_txt"):
        return ocr(src, t, work, stem, progress)
    if t in ("pdf_png", "pdf_jpg"):
        return pdf_to_images(src, t[-3:], work, stem, progress)
    if t == "pdf_small":
        dst = work / f"{stem}-compressed.pdf"
        progress(None, "Compressing")
        if TOOLS["gs"]:
            run([TOOLS["gs"], "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6", "-dPDFSETTINGS=/ebook",
                 "-dNOPAUSE", "-dQUIET", "-dBATCH", "-dDetectDuplicateImages=true",
                 f"-sOutputFile={dst}", str(src)])
        else:   # PyMuPDF: images over 150 dpi resampled to 150 dpi JPEG, duplicate objects merged
            with pymupdf.open(src) as d:
                d.rewrite_images(dpi_threshold=160, dpi_target=150, quality=70)
                d.save(dst, garbage=4, deflate=True, use_objstms=True)
        if dst.stat().st_size >= src.stat().st_size:
            shutil.copy(src, dst)  # already optimal; never make it bigger
        return dst
    if t == "docx":
        from pdf2docx import Converter
        dst = work / f"{stem}.docx"
        progress(None, "Rebuilding layout as editable Word")
        cv = Converter(str(src))
        try:
            cv.convert(str(dst), multi_processing=False)
        finally:
            cv.close()
        return dst
    doc = pymupdf.open(src)
    if t == "txt":
        dst = work / f"{stem}.txt"
        dst.write_text("\n\f\n".join(p.get_text("text") for p in doc))
        if not dst.read_text().strip() and TOOLS["tesseract"]:
            return ocr(src, "ocr_txt", work, stem, progress)  # scanned PDF → OCR
        return dst
    if t == "html":
        dst = work / f"{stem}.html"
        body = "\n".join(f'<section class="page">{p.get_text("html")}</section>' for p in doc)
        dst.write_text(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{stem}</title>"
                       "<style>body{background:#eee;margin:0;padding:24px}.page{background:#fff;"
                       "margin:0 auto 24px;box-shadow:0 1px 4px #0002;width:fit-content}</style>"
                       f"</head><body>{body}</body></html>")
        return dst
    if t == "md":
        dst = work / f"{stem}.md"
        try:
            import pymupdf4llm  # optional, better structure
            dst.write_text(pymupdf4llm.to_markdown(str(src), show_progress=False).replace("\ufffd", ""))
        except Exception:
            parts = []
            for p in doc:
                for b in p.get_text("dict")["blocks"]:
                    for line in b.get("lines", []):
                        spans = line["spans"]
                        txt = "".join(s["text"] for s in spans).strip()
                        if not txt:
                            continue
                        size = max(s["size"] for s in spans)
                        bold = any(s["flags"] & 16 for s in spans)
                        if size >= 18:
                            parts.append(f"\n# {txt}\n")
                        elif size >= 14 or (bold and len(txt) < 80):
                            parts.append(f"\n## {txt}\n")
                        else:
                            parts.append(txt)
                    parts.append("")
            dst.write_text(re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip() + "\n")
        return dst
    raise ConvertError(f"Unsupported PDF target {t}")


LO_FMT = {"pdf": "pdf", "docx": "docx:MS Word 2007 XML", "odt": "odt", "rtf": "rtf",
          "doc": "doc:MS Word 97", "txt": "txt:Text", "html": "html:XHTML Writer File:UTF8",
          "pptx": "pptx:Impress MS PowerPoint 2007 XML", "odp": "odp", "ppt": "ppt:MS PowerPoint 97",
          "xlsx": "xlsx:Calc MS Excel 2007 XML", "ods": "ods", "xls": "xls:MS Excel 97"}


def _rename(p: Path, dst: Path) -> Path:
    if p != dst:
        shutil.move(p, dst)
    return dst


def conv_office(src: Path, t: str, work: Path, stem: str, progress: Progress) -> Path:
    progress(None, "Rendering with LibreOffice")
    lo = work / "lo"
    if t in ("pdf_png", "pdf_jpg"):
        pdf = soffice(src, "pdf", lo)
        return pdf_to_images(pdf, t[-3:], work, stem, progress)
    if t in ("md", "epub", "txt") and TOOLS["pandoc"]:  # Word-ish -> pandoc via docx
        mid = src if src.suffix.lower() in (".docx", ".odt") else soffice(src, LO_FMT["docx"], lo)
        progress(0.5, "Restructuring with Pandoc")
        media = ["--extract-media", str(work / f"{stem}_media")] if t == "md" else []
        to = {"md": ["-t", "gfm"], "txt": ["-t", "plain", "--wrap=none"]}.get(t, [])
        out = pandoc(mid, work / f"{stem}.{T[t][0]}", extra=media + to)
        if t == "md" and (work / f"{stem}_media").exists():
            files = [out] + [p for p in (work / f"{stem}_media").rglob("*") if p.is_file()]
            return zip_files(files, work / f"{stem}-markdown.zip")
        return out
    out = soffice(src, LO_FMT[t], lo)
    return _rename(out, work / f"{stem}.{T[t][0]}")


def _read_table(src: Path) -> dict:
    import pandas as pd
    ext = src.suffix.lower()
    if ext == ".csv":
        return {src.stem: pd.read_csv(src, sep=None, engine="python")}
    if ext == ".tsv":
        return {src.stem: pd.read_csv(src, sep="\t")}
    if ext == ".json":
        data = json.loads(src.read_text())
        if isinstance(data, dict) and all(isinstance(v, list) for v in data.values()) and data:
            return {k: pd.json_normalize(v) for k, v in data.items()}
        if isinstance(data, dict):
            data = [data]
        return {src.stem: pd.json_normalize(data)}
    if ext == ".numbers":
        raise ConvertError("needs-lo")
    engine = "odf" if ext == ".ods" else None
    return pd.read_excel(src, sheet_name=None, engine=engine)


def conv_table(src: Path, t: str, work: Path, stem: str, progress: Progress) -> Path:
    import pandas as pd
    ext = T[t][0]
    if t in ("pdf", "ods", "xls") or (t == "xlsx" and src.suffix.lower() in (".ods", ".xls", ".numbers", ".xlsm", ".csv", ".tsv")):
        if TOOLS["soffice"] and src.suffix.lower() != ".json":
            return conv_office(src, t, work, stem, progress)
    progress(None, "Reading tables")
    try:
        sheets = _read_table(src)
    except ConvertError:
        mid = soffice(src, LO_FMT["xlsx"], work / "lo")
        sheets = pd.read_excel(mid, sheet_name=None)
    sheets = {k: v for k, v in sheets.items() if not v.empty} or sheets
    progress(0.5, "Writing")
    dst = work / f"{stem}.{ext}"
    if ext == "xlsx":
        with pd.ExcelWriter(dst, engine="openpyxl") as xw:
            for name, df in sheets.items():
                df.to_excel(xw, sheet_name=str(name)[:31] or "Sheet1", index=False)
                ws = xw.sheets[str(name)[:31] or "Sheet1"]
                for col in ws.columns:  # auto-width
                    width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                    ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 60)
        return dst
    if ext == "json":
        if len(sheets) == 1:
            payload = next(iter(sheets.values())).to_dict(orient="records")
        else:
            payload = {k: v.to_dict(orient="records") for k, v in sheets.items()}
        dst.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return dst
    if ext in ("csv", "tsv"):
        sep = "," if ext == "csv" else "\t"
        if len(sheets) == 1:
            next(iter(sheets.values())).to_csv(dst, index=False, sep=sep)
            return dst
        files = []
        for name, df in sheets.items():
            safe = re.sub(r"[^\w.-]+", "_", str(name))
            f = work / f"{stem}-{safe}.{ext}"
            df.to_csv(f, index=False, sep=sep)
            files.append(f)
        return zip_files(files, work / f"{stem}-{ext}.zip")
    if ext == "md":
        dst.write_text("\n\n".join((f"## {k}\n\n" if len(sheets) > 1 else "") +
                                   v.to_markdown(index=False) for k, v in sheets.items()))
        return dst
    if ext == "html":
        tables = "".join((f"<h2>{k}</h2>" if len(sheets) > 1 else "") +
                         v.to_html(index=False, border=0, na_rep="") for k, v in sheets.items())
        dst.write_text("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                       f"<title>{stem}</title><style>body{{font:14px -apple-system,sans-serif;padding:24px}}"
                       "table{border-collapse:collapse;margin-bottom:32px}th,td{border:1px solid #ddd;"
                       "padding:6px 10px;text-align:left}th{background:#111;color:#fff}"
                       "tr:nth-child(even){background:#f6f6f6}</style></head><body>"
                       f"{tables}</body></html>")
        return dst
    raise ConvertError(f"Unsupported target {t}")


def conv_text(src: Path, t: str, work: Path, stem: str, progress: Progress) -> Path:
    ext = T[t][0]
    sext = src.suffix.lower().lstrip(".")
    frm = PANDOC_IN.get(sext, "markdown")
    if sext == "txt" and (t in ("pdf", "docx", "odt", "rtf") or not TOOLS["pandoc"]):
        return conv_office(src, t, work, stem, progress)
    progress(None, "Converting with Pandoc")
    if ext in ("pdf", "rtf") and TOOLS["soffice"]:
        mid = pandoc(src, work / f"{stem}.__mid.docx", frm)
        progress(0.5, "Typesetting")
        out = soffice(mid, LO_FMT[ext], work / "lo")
        return _rename(out, work / f"{stem}.{ext}")
    to = {"md": ["-t", "gfm"], "txt": ["-t", "plain", "--wrap=none"], "tex": ["-t", "latex"]}.get(t, [])
    return pandoc(src, work / f"{stem}.{ext}", frm, extra=to)


# ---- audio / video

def media_duration(src: Path) -> float | None:
    if not TOOLS["ffprobe"]:
        return None
    try:
        out = run([TOOLS["ffprobe"], "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=nw=1:nk=1", str(src)], timeout=60)
        return float(out.strip())
    except Exception:
        return None


def ffmpeg(args: list[str], src: Path, dst: Path, progress: Progress, label: str) -> Path:
    dur = media_duration(src)
    cmd = [TOOLS["ffmpeg"], "-hide_banner", "-y", "-i", str(src), *args,
           "-progress", "pipe:1", "-nostats", str(dst)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err_lines: list[str] = []

    def drain():
        for line in p.stderr:
            err_lines.append(line)
            del err_lines[:-20]

    th = threading.Thread(target=drain, daemon=True)
    th.start()
    for line in p.stdout:
        if line.startswith("out_time_us=") and dur:
            try:
                progress(min(int(line.split("=")[1]) / 1e6 / dur, 0.99), label)
            except ValueError:
                pass
    p.wait()
    th.join(timeout=2)
    if p.returncode != 0:
        raise ConvertError("ffmpeg failed: " + " ".join(l.strip() for l in err_lines[-3:])[:400])
    return dst


AUDIO_ARGS = {
    "mp3": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
    "wav": ["-vn", "-c:a", "pcm_s16le"],
    "m4a": ["-vn", "-c:a", "aac", "-b:a", "256k"],
    "aac": ["-vn", "-c:a", "aac", "-b:a", "256k"],
    "flac": ["-vn", "-c:a", "flac"],
    "ogg": ["-vn", "-c:a", "libvorbis", "-q:a", "6"],
    "opus": ["-vn", "-c:a", "libopus", "-b:a", "160k"],
    "aiff": ["-vn", "-c:a", "pcm_s16be"],
}
VIDEO_ARGS = {
    "mp4": ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    "mov": ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k"],
    "mkv": ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-deadline", "good",
             "-cpu-used", "4", "-c:a", "libopus", "-b:a", "128k"],
    "avi": ["-c:v", "mpeg4", "-q:v", "3", "-c:a", "libmp3lame", "-q:a", "3"],
}


def conv_media(src: Path, t: str, work: Path, stem: str, progress: Progress) -> Path:
    if not TOOLS["ffmpeg"]:
        raise ConvertError("FFmpeg is not installed")
    ext = T[t][0]
    dst = work / f"{stem}.{ext}"
    if t == "vgif":
        dur = media_duration(src) or 0
        clip = ["-t", "30"] if dur > 30 else []
        palette = work / "__pal.png"
        vf = "fps=15,scale='min(720,iw)':-2:flags=lanczos"
        ffmpeg(clip + ["-vf", vf + ",palettegen=stats_mode=diff"], src, palette, progress, "Building palette")
        cmd = [TOOLS["ffmpeg"], "-hide_banner", "-y", *clip, "-i", str(src), "-i", str(palette),
               "-lavfi", vf + " [x]; [x][1:v] paletteuse=dither=sierra2_4a", str(dst)]
        progress(0.5, "Encoding GIF (first 30s)" if clip else "Encoding GIF")
        run(cmd)
        return dst
    if t == "thumb":
        dur = media_duration(src) or 0
        run([TOOLS["ffmpeg"], "-hide_banner", "-y", "-ss", str(min(dur * 0.1, 5)), "-i", str(src),
             "-frames:v", "1", "-q:v", "2", str(dst)])
        return dst
    args = AUDIO_ARGS.get(ext) or VIDEO_ARGS[ext]
    if ext == "ogg" and not _has_encoder("libvorbis"):
        args = ["-vn", "-c:a", "libopus", "-b:a", "160k"]  # Ogg container, Opus codec
    return ffmpeg(args, src, dst, progress, "Encoding")


_encoders: str | None = None


def _has_encoder(name: str) -> bool:
    global _encoders
    if _encoders is None:
        _encoders = run([TOOLS["ffmpeg"], "-hide_banner", "-encoders"], timeout=30)
    return f" {name} " in _encoders


# --------------------------------------------------------------------------- dispatch

def convert(src: Path, original_name: str, t: str, work: Path, progress: Progress) -> Path:
    if t not in targets_for(original_name):
        raise ConvertError(f"Can't convert {Path(original_name).suffix} to {T.get(t, (t,))[0]}")
    stem = Path(original_name).stem or "file"
    stem = re.sub(r"[^\w.\- ]+", "_", stem).strip() or "file"
    k = kind_of(original_name)
    work.mkdir(parents=True, exist_ok=True)
    if k == "image":
        if t in ("ocr_txt", "pdf_ocr"):
            return ocr(src, t, work, stem, progress)
        return conv_image(src, t, work, stem, progress)
    if k == "vector":
        return conv_svg(src, t, work, stem, progress)
    if k == "pdf":
        return conv_pdf(src, t, work, stem, progress)
    if k in ("document", "slides"):
        return conv_office(src, t, work, stem, progress)
    if k in ("spreadsheet", "table", "data"):
        return conv_table(src, t, work, stem, progress)
    if k == "text":
        return conv_text(src, t, work, stem, progress)
    if k in ("audio", "video"):
        return conv_media(src, t, work, stem, progress)
    raise ConvertError("Unsupported file type")
