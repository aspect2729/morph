"""Morph PDF editor engine.

The browser keeps the editing state; this module
  * opens anything as a PDF (PDF, images, Office/text documents),
  * renders pages,
  * extracts editable text lines (real PDF text, or Tesseract OCR for scanned/image pages)
    with font, size, colour and the background colour behind them,
  * rebuilds the final PDF from the page list + edits.

All coordinates exchanged with the browser are PDF points in *display* space
(the page as it appears, i.e. after the page's own /Rotate).
"""
from __future__ import annotations

import base64
import io
import re
import shutil
import subprocess
import uuid
from functools import lru_cache
from pathlib import Path

import numpy as np
import pymupdf

from . import config, engine, fonts, scanedit

SESSIONS: dict[str, dict] = {}

SYS = Path("/System/Library/Fonts/Supplemental")
FONT_FILES = {
    ("sans", False, False): "Arial.ttf", ("sans", True, False): "Arial Bold.ttf",
    ("sans", False, True): "Arial Italic.ttf", ("sans", True, True): "Arial Bold Italic.ttf",
    ("serif", False, False): "Times New Roman.ttf", ("serif", True, False): "Times New Roman Bold.ttf",
    ("serif", False, True): "Times New Roman Italic.ttf", ("serif", True, True): "Times New Roman Bold Italic.ttf",
    ("mono", False, False): "Courier New.ttf", ("mono", True, False): "Courier New Bold.ttf",
    ("mono", False, True): "Courier New Italic.ttf", ("mono", True, True): "Courier New Bold Italic.ttf",
}
BASE14 = {
    ("sans", False, False): "helv", ("sans", True, False): "hebo", ("sans", False, True): "heit", ("sans", True, True): "hebi",
    ("serif", False, False): "tiro", ("serif", True, False): "tibo", ("serif", False, True): "tiit", ("serif", True, True): "tibi",
    ("mono", False, False): "cour", ("mono", True, False): "cobo", ("mono", False, True): "coit", ("mono", True, True): "cobi",
}


class EditError(Exception):
    pass


# ----------------------------------------------------------------------------- sessions

def new_session(root: Path, ws: str = "local") -> str:
    sid = "ed" + uuid.uuid4().hex[:24]
    d = root / sid
    d.mkdir(parents=True)
    SESSIONS[sid] = {"dir": d, "sources": {}, "ws": ws, "pages": 0}
    return sid


def session(sid: str) -> dict:
    s = SESSIONS.get(sid)
    if not s or not s["dir"].exists():
        raise EditError("Editing session expired. Open the file again.")
    return s


def _to_pdf(src: Path, name: str, work: Path) -> Path:
    """Turn any supported file into a PDF."""
    ext = Path(name).suffix.lower().lstrip(".")
    kind = engine.kind_of(name)
    out = work / (uuid.uuid4().hex[:8] + ".pdf")
    if ext == "pdf":
        shutil.copy(src, out)
    elif kind == "image":
        from PIL import Image, ImageOps
        import pillow_heif
        pillow_heif.register_heif_opener()
        im = ImageOps.exif_transpose(Image.open(src))
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, "white")
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        # place the photo on a page at 150 dpi so it prints at a sensible size
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=95)
        w, h = im.size[0] * 72 / 150, im.size[1] * 72 / 150
        doc = pymupdf.open()
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, stream=buf.getvalue())
        doc.save(out)
    elif kind == "vector":
        engine.svg_pdf(src, out)
    elif kind in ("document", "slides", "spreadsheet", "table", "text"):
        tmp = work / "lo"
        res = engine.conv_office(src, "pdf", tmp, "doc", lambda *a: None) if kind != "text" \
            else engine.conv_text(src, "pdf", tmp, "doc", lambda *a: None)
        shutil.move(res, out)
    else:
        raise EditError("This file type can't be opened in the editor. Try a PDF, image or document.")
    return out


def add_source(sid: str, src: Path, name: str) -> dict:
    s = session(sid)
    pdf = _to_pdf(src, name, s["dir"])
    doc = pymupdf.open(pdf)
    if doc.needs_pass and not doc.authenticate(""):
        raise EditError("This PDF is password-protected.")
    if s["pages"] + doc.page_count > config.MAX_PAGES:
        raise EditError(f"That's too many pages to edit at once (limit {config.MAX_PAGES}).")
    s["pages"] += doc.page_count
    srcid = "s" + uuid.uuid4().hex[:6]
    s["sources"][srcid] = {"path": pdf, "name": name}
    pages = []
    for p in doc:
        text = p.get_text("text").strip()
        pages.append({"w": round(p.rect.width, 2), "h": round(p.rect.height, 2),
                      "hasText": bool(text), "scanned": not text and bool(p.get_images())})
    return {"src": srcid, "name": name, "pages": pages}


def _doc(sid: str, srcid: str) -> pymupdf.Document:
    s = session(sid)
    if srcid not in s["sources"]:
        raise EditError("Unknown page source")
    return _open_cached(str(s["sources"][srcid]["path"]))


@lru_cache(maxsize=32)
def _open_cached(path: str) -> pymupdf.Document:
    return pymupdf.open(path)


# ----------------------------------------------------------------------------- rendering

def render(sid: str, srcid: str, index: int, width: int) -> bytes:
    s = session(sid)
    width = max(80, min(int(width), 3200))
    cache = s["dir"] / f"r-{srcid}-{index}-{width}.png"
    if cache.exists():
        return cache.read_bytes()
    page = _doc(sid, srcid)[index]
    zoom = width / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    data = pix.tobytes("png")
    cache.write_bytes(data)
    return data


def _page_array(page: pymupdf.Page, dpi: int) -> tuple[np.ndarray, float]:
    pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    return arr, dpi / 72


def _colors(arr: np.ndarray, scale: float, bbox) -> tuple[str, str]:
    """Background colour (around the box) and ink colour (inside) for a display-space bbox."""
    h, w, _ = arr.shape
    x0, y0, x1, y1 = [int(round(v * scale)) for v in bbox]
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w - 1), min(y1, h - 1)
    if x1 <= x0 or y1 <= y0:
        return "#ffffff", "#000000"
    pad = max(2, int(scale * 1.5))
    ring = np.concatenate([
        arr[max(y0 - pad, 0):y0, x0:x1].reshape(-1, 3),
        arr[y1:min(y1 + pad, h), x0:x1].reshape(-1, 3),
        arr[y0:y1, max(x0 - pad, 0):x0].reshape(-1, 3),
        arr[y0:y1, x1:min(x1 + pad, w)].reshape(-1, 3),
    ])
    inside = arr[y0:y1, x0:x1].reshape(-1, 3).astype(int)
    if len(ring) == 0:
        ring = inside
    # most common (quantised) colour of the surround = paper
    q = (ring // 8).astype(int)
    keys = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    top = vals[counts.argmax()]
    bg = ring[keys == top].mean(axis=0)
    # ink = the pixels inside that differ most from the paper
    dist = np.abs(inside - bg).sum(axis=1)
    if dist.max() < 40:
        fg = np.array([0, 0, 0])
    else:
        core = inside[dist >= dist.max() * 0.8]          # centre of the strokes, not the blurry edges
        fg = core.mean(axis=0)
        # scanners lift black ink to grey; push dark, low-saturation ink back towards true black
        if fg.max() - fg.min() < 40 and fg.mean() < 150:
            fg = fg * 0.8
    hexc = lambda c: "#%02x%02x%02x" % tuple(int(max(0, min(255, v))) for v in c)
    return hexc(bg), hexc(fg)


_SANS = ("sans", "carlito", "calibri", "arial", "helvetica", "lato", "roboto", "montserrat", "inter", "segoe",
         "verdana", "tahoma", "poppins", "nunito", "raleway", "ubuntu", "futura", "avenir", "gill", "franklin",
         "trebuchet", "gothic", "frutiger", "myriad", "univers", "proxima", "manrope", "rubik", "worksans", "dmsans",
         "figtree", "outfit", "karla", "mulish", "barlow", "oswald", "cantarell", "aptos", "corbel", "candara")
_SERIF = ("serif", "times", "georgia", "garamond", "cambria", "caladea", "roman", "minion", "palatino",
          "baskerville", "merriweather", "playfair", "lora", "bodoni", "didot", "charter", "constantia", "book")


def _family(fontname: str, flags: int) -> tuple[str, bool, bool]:
    n = fontname.lower()
    mono = flags & 8 or any(k in n for k in ("mono", "courier", "consol", "menlo", "code"))
    # trust the font's name first: converters (LibreOffice) often set the PDF "serif" flag on sans fonts
    k = re.sub(r"[^a-z]", "", n)
    if any(s in k for s in _SANS):
        serif = False
    elif any(s in k for s in _SERIF):
        serif = True
    else:
        serif = bool(flags & 4)
    fam = "mono" if mono else ("serif" if serif else "sans")
    bold = bool(flags & 16) or any(k in n for k in ("bold", "black", "heavy", "semibold"))
    italic = bool(flags & 2) or "italic" in n or "oblique" in n
    return fam, bold, italic


def lines(sid: str, srcid: str, index: int, ocr: bool = False) -> dict:
    page = _doc(sid, srcid)[index]
    rot = page.rotation_matrix
    out = []
    arr, scale = _page_array(page, 150 if ocr else 110)
    if not ocr:
        fonts = {}
        for f in page.get_fonts(full=True):
            base = re.sub(r"^[A-Z]{6}\+", "", f[3] or "")
            fonts.setdefault(base, f[0])
        d = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
        for b in d["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                spans = [s for s in ln["spans"] if s["text"].strip()]
                if not spans:
                    continue
                dx, dy = ln["dir"]
                v = pymupdf.Point(dx, dy) * pymupdf.Matrix(rot.a, rot.b, rot.c, rot.d, 0, 0)
                if abs(v.x - 1) > 0.02:   # only text that reads left-to-right on screen is editable
                    continue
                text = "".join(s["text"] for s in ln["spans"]).strip()
                main = max(spans, key=lambda s: len(s["text"]))
                bbox = pymupdf.Rect(ln["bbox"]) * rot
                origin = pymupdf.Point(spans[0]["origin"]) * rot
                fam, bold, italic = _family(main["font"], main["flags"])
                bg, _ = _colors(arr, scale, bbox)
                c = main["color"]
                out.append({
                    "id": f"l{len(out)}", "source": "pdf", "text": text,
                    "bbox": [round(v, 2) for v in bbox], "origin": [round(origin.x, 2), round(origin.y, 2)],
                    "size": round(main["size"], 2), "color": "#%06x" % c, "bg": bg,
                    "family": fam, "bold": bold, "italic": italic,
                    "font": re.sub(r"^[A-Z]{6}\+", "", main["font"]),
                    "fontxref": fonts.get(re.sub(r"^[A-Z]{6}\+", "", main["font"]), 0),
                })
        return {"lines": out, "ocr": False}

    # ---- OCR: Tesseract (LSTM) with per-character boxes → a page model that can rewrite the scan
    if not engine.TOOLS["tesseract"]:
        raise EditError("Tesseract OCR is not installed")
    m = _scan_model(sid, srcid, index, page, fresh=True)
    for i, ln in enumerate(m.lines):
        xs = [g.box for g in ln.chars]
        X0, Y0, X1, Y1 = min(b[0] for b in xs), min(b[1] for b in xs), max(b[2] for b in xs), max(b[3] for b in xs)
        x0, y0 = m.cv.pt(X0, Y0)
        x1, y1 = m.cv.pt(X1, Y1)
        cap_pt = ln.cap / m.cv.sy
        bg, _ = _colors(arr, scale, (x0, y0, x1, y1))
        out.append({
            "id": f"o{i}", "source": "ocr", "text": ln.text,
            "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
            "origin": [round(x0, 2), round(m.cv.pt(0, ln.baseline(X0))[1], 2)],
            "size": round(cap_pt / 0.716, 1), "color": "#%02x%02x%02x" % tuple(int(v) for v in ln.ink),
            "bg": bg, "family": getattr(ln, "family", "sans"), "bold": bool(getattr(ln, "bold", False)),
            "italic": False, "font": "Scanned text", "fontxref": 0,
        })
    _snap_sizes(out)
    return {"lines": out, "ocr": True}


def _snap_sizes(lines_: list[dict]) -> None:
    """Scanned pages usually use a handful of font sizes. Per-line estimates are noisy (±10%),
    so cluster them and snap each line to its cluster's median — table cells end up consistent."""
    sizes = sorted(l["size"] for l in lines_)
    if len(sizes) < 4:
        return
    clusters, cur = [], [sizes[0]]
    for v in sizes[1:]:
        if v <= cur[-1] * 1.07:
            cur.append(v)
        else:
            clusters.append(cur); cur = [v]
    clusters.append(cur)
    centers = [float(np.median(c)) for c in clusters if len(c) >= 3]
    for l in lines_:
        if not centers:
            break
        c = min(centers, key=lambda m: abs(m - l["size"]))
        if abs(c - l["size"]) / c < 0.22:
            l["size"] = round(c, 1)


def _scan_model(sid: str, srcid: str, index: int, page: pymupdf.Page | None = None, fresh: bool = False):
    key = (sid, srcid, index)
    m = None if fresh else scanedit.get(key)
    if m is None:
        page = page or _doc(sid, srcid)[index]
        s = session(sid)
        img = s["dir"] / f"ocr-{srcid}-{index}.png"
        page.get_pixmap(dpi=scanedit.OCR_DPI, alpha=False).save(str(img))
        m = scanedit.PageModel(page, scanedit.ocr(img, engine.TOOLS["tesseract"]))
        scanedit.put(key, m)
    return m


def _scan_line(m, line: dict):
    """The model line a client line refers to (by id, verified by text/box)."""
    lid = line.get("id", "")
    if lid.startswith("o") and lid[1:].isdigit():
        i = int(lid[1:])
        if i < len(m.lines) and m.lines[i].text == line.get("text"):
            return m.lines[i]
    return m.find(line["bbox"])


def _scan_style(e: dict, ln: dict) -> dict | None:
    """None = clone the scan's own glyphs; a dict = the user restyled the line."""
    changed = {k: e.get(k) for k in ("family", "bold", "italic") if e.get(k) is not None and e.get(k) != ln.get(k)}
    st = {}
    if changed:
        st.update(family=e.get("family", "sans"), bold=bool(e.get("bold")), italic=bool(e.get("italic")))
    if e.get("size") and abs(float(e["size"]) - float(ln["size"])) > 0.05:
        st["size"] = float(e["size"]); st.setdefault("family", e.get("family", "sans"))
    if e.get("color") and e["color"].lower() != (ln.get("color") or "").lower():
        st["color"] = e["color"]
    return st or None


PREVIEW_SCALE = 3          # patch pixels per PDF point (sharp at 300% zoom on a retina screen)


def _preview_pdf(sid: str, srcid: str, index: int, e: dict) -> dict:
    """A digital-text edit drawn exactly as Save will write it (same fonts, real background, real baseline):
    the edit is applied to a one-page copy and the changed area is returned as a PNG patch."""
    src = _doc(sid, srcid)
    ln = e["line"]
    doc = pymupdf.open()
    doc.insert_pdf(src, from_page=index, to_page=index)
    page = doc[0]
    edit = dict(e, type="text_edit")
    fonts = _Fonts(page, src)
    width = _line_runs(fonts, edit)[2] if edit.get("text", "").strip() else 0
    _apply(page, [edit], src)
    x0, y0, x1, y1 = ln["bbox"]
    ox = ln["origin"][0]
    # tight to the line's own box (+1pt), so it never covers a neighbouring line that may be edited too
    area = pymupdf.Rect(min(x0, ox) - 2, y0 - 1, max(x1, ox + width) + 2, y1 + 1) & page.rect
    if area.is_empty:
        return {"img": None}
    m = pymupdf.Matrix(PREVIEW_SCALE, PREVIEW_SCALE)
    if page.rotation == 0:
        pix = page.get_pixmap(matrix=m, clip=area, alpha=False)
        png = pix.tobytes("png")
    else:                      # rotated page: render it upright, then cut the area out in display space
        from PIL import Image
        pix = page.get_pixmap(matrix=m, alpha=False)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        box = tuple(int(round(v * PREVIEW_SCALE)) for v in area)
        buf = io.BytesIO(); im.crop(box).save(buf, "PNG"); png = buf.getvalue()
    return {"img": "data:image/png;base64," + base64.b64encode(png).decode(),
            "rect": [round(v, 2) for v in area]}


def preview(sid: str, srcid: str, index: int, e: dict) -> dict:
    """Render one line edit exactly as it will be saved; returns a PNG patch + its rect."""
    if e.get("line", {}).get("source") == "pdf":
        return _preview_pdf(sid, srcid, index, e)
    m = _scan_model(sid, srcid, index)
    ln = _scan_line(m, e["line"])
    if ln is None:
        raise EditError("Couldn't find that line in the scan — try Re-read text (OCR)")
    with m.lock:
        try:
            box = m.edit(ln, e.get("text", ""), _scan_style(e, e["line"]))
            if not any(box):
                return {"img": None}
            png, rect = m.patch_png(box)
        finally:
            m.cv.revert()
    return {"img": "data:image/png;base64," + base64.b64encode(png).decode(), "rect": [round(v, 2) for v in rect]}


def _em_from_height(h: float, text: str) -> float:
    """Ink height of a line -> font size, based on which glyph zones the text uses."""
    asc = any(c.isupper() or c.isdigit() or c in "bdfhiklt'\"!?/" for c in text)
    desc = any(c in "gjpqy,;()[]{}|Q" for c in text)
    if asc and desc:
        ratio = 0.93
    elif asc:
        ratio = 0.72
    elif desc:
        ratio = 0.72
    else:
        ratio = 0.5
    return h / ratio


# ----------------------------------------------------------------------------- building

def _rgb(hexc: str | None, default=(0, 0, 0)):
    if not hexc:
        return default
    h = hexc.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


class _Fonts:
    """Registers fonts on a page and picks the best one for a string."""

    def __init__(self, page: pymupdf.Page, src_doc: pymupdf.Document | None):
        self.page = page
        self.src = src_doc
        self.done: set[str] = set()
        self.objs: dict[str, pymupdf.Font] = {}
        self.bufs: dict[str, bytes] = {}      # embedded fonts, registered on first use
        self.files: dict[str, str] = {}       # installed font files, registered on first use

    def _register(self, name: str, **kw) -> str:
        if name not in self.done:
            self.page.insert_font(fontname=name, **kw)
            self.done.add(name)
        return name

    def embedded(self, fontxref: int):
        """The document's own (usually subset) font, or None."""
        if not fontxref or self.src is None:
            return None
        name = f"orig{fontxref}"
        if name not in self.objs:
            try:
                _, ext, _, buf = self.src.extract_font(fontxref)
                if not buf or ext in ("n/a", "pfa"):
                    return None
                self.objs[name] = pymupdf.Font(fontbuffer=buf)
                self.bufs[name] = buf
            except Exception:
                return None
        return name, self.objs[name]

    def named(self, pdf_font: str, bold: bool, italic: bool):
        """The complete installed font of the same family (or its metric twin, e.g. Calibri -> Carlito)."""
        p = fonts.by_name(pdf_font or "", bool(bold), bool(italic))
        if p is None:
            return None
        name = "full" + re.sub(r"[^A-Za-z0-9]", "", p.stem)[:24]
        if name not in self.objs:
            self.objs[name] = pymupdf.Font(fontfile=str(p))
            self.files[name] = str(p)
        return name, self.objs[name]

    def use(self, name: str) -> str:
        """Make sure a picked font is embedded in the page before text is written with it."""
        if name in self.bufs:
            return self._register(name, fontbuffer=self.bufs[name])
        if name in self.files:
            return self._register(name, fontfile=self.files[name])
        return name

    @staticmethod
    def covers(f: pymupdf.Font, text: str) -> bool:
        return all(f.has_glyph(ord(ch)) for ch in text if not ch.isspace())

    def runs(self, text: str, ln: dict, family: str, bold: bool, italic: bool):
        """[(word, fontname, Font)] for an edited line, changing the look as little as possible:
        1. the document's embedded font when it has every glyph typed;
        2. else the complete installed font of the same family (subsets often lack new letters);
        3. else keep the embedded font for the words it can draw, and a lookalike only for the rest."""
        same_family = family == ln.get("family")
        same_style = same_family and bool(bold) == bool(ln.get("bold")) and bool(italic) == bool(ln.get("italic"))
        emb = self.embedded(ln.get("fontxref", 0)) if same_style else None
        full = self.named(ln.get("font", ""), bold, italic) if same_family else None
        words = text.split(" ")
        if emb and self.covers(emb[1], text):
            return [(w, *emb) for w in words]
        if full and self.covers(full[1], text):
            return [(w, *full) for w in words]
        generic = self.pick(text, family, bold, italic)
        alt = full or generic
        out = []
        for w in words:
            if emb and self.covers(emb[1], w):
                out.append((w, *emb))
            elif full and self.covers(full[1], w):
                out.append((w, *full))
            else:
                out.append((w, *(alt if self.covers(alt[1], w) else generic)))
        return out

    def pick(self, text: str, family: str, bold: bool, italic: bool, fontxref: int = 0):
        # 1. the document's own embedded font, if it has every glyph we need
        emb = self.embedded(fontxref)
        if emb and self.covers(emb[1], text):
            return emb
        key = (family if family in ("sans", "serif", "mono") else "sans", bool(bold), bool(italic))
        # 2. matching system TrueType font (full Unicode Latin, Greek, Cyrillic…)
        path = fonts.path(*key)
        if path is not None:
            name = "sys" + "".join(str(int(x)) if isinstance(x, bool) else x for x in key)
            self._register(name, fontfile=str(path))
            if name not in self.objs:
                self.objs[name] = pymupdf.Font(fontfile=str(path))
            return name, self.objs[name]
        # 3. PDF base-14
        name = BASE14[key]
        return name, pymupdf.Font(name)


def _derot(page: pymupdf.Page, rect) -> pymupdf.Rect:
    return pymupdf.Rect(rect) * page.derotation_matrix


def _apply(page: pymupdf.Page, edits: list[dict], src_doc) -> None:
    fonts = _Fonts(page, src_doc)
    rot = page.rotation
    redact = False
    # pass 1: remove original text being replaced, and true redactions
    for e in edits:
        if e["type"] == "text_edit" and e["line"]["source"] == "pdf":
            r = _derot(page, e["line"]["bbox"])
            page.add_redact_annot(r + (-0.5, -0.5, 0.5, 0.5), fill=False)
            redact = True
        elif e["type"] == "redact":
            page.add_redact_annot(_derot(page, e["rect"]), fill=(0, 0, 0))
            redact = True
    if redact:
        # text-only for edits keeps images/vector art intact; explicit redactions also wipe pixels
        has_true = any(e["type"] == "redact" for e in edits)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS if has_true else pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)

    for e in edits:
        t = e["type"]
        if t == "whiteout":
            page.draw_rect(_derot(page, e["rect"]), color=None, fill=_rgb(e.get("color"), (1, 1, 1)), overlay=True)
        elif t == "text_edit":
            ln = e["line"]
            text = e.get("text", "").replace("\n", " ")
            color = _rgb(e.get("color") or ln["color"])
            if ln["source"] == "ocr":   # paint over the pixels with the paper colour
                bb = pymupdf.Rect(ln["bbox"]) + (-1.5, -1.5, 1.5, 1.5)
                page.draw_rect(_derot(page, bb), color=None, fill=_rgb(ln.get("bg"), (1, 1, 1)), overlay=True)
            if not text.strip():
                continue
            runs, size, width = _line_runs(fonts, e)
            _put_runs(page, fonts, pymupdf.Point(ln["origin"]), runs, size, color, rot)
        elif t == "text":
            text = e.get("text", "")
            if not text.strip():
                continue
            r = pymupdf.Rect(e["rect"])
            size = float(e.get("size", 14))
            name, fobj = fonts.pick(text, e.get("family", "sans"), e.get("bold"), e.get("italic"))
            lh = size * 1.25
            y = r.y0 + size * 0.95
            align = {"left": 0, "center": 1, "right": 2}.get(e.get("align", "left"), 0)
            for para in text.split("\n"):
                for line in _wrap(para, fobj, size, r.width):
                    w = fobj.text_length(line, fontsize=size)
                    x = r.x0 + (0 if align == 0 else (r.width - w) / (2 if align == 1 else 1))
                    _put(page, pymupdf.Point(x, y), line, size, name, fobj, _rgb(e.get("color")), rot)
                    y += lh
        elif t == "image":
            data = e["data"].split(",", 1)[-1]
            page.insert_image(_derot(page, e["rect"]), stream=base64.b64decode(data), keep_proportion=False,
                              rotate=(-rot) % 360 if rot else 0)
        elif t == "highlight":
            a = page.add_highlight_annot(_derot(page, e["rect"]))
            a.set_colors(stroke=_rgb(e.get("color"), (1, 0.89, 0.2)))
            a.update()
        elif t == "ink":
            pts = [[pymupdf.Point(x, y) * page.derotation_matrix for x, y in path] for path in e["paths"] if len(path) > 1]
            if pts:
                a = page.add_ink_annot([[(p.x, p.y) for p in path] for path in pts])
                a.set_border(width=float(e.get("width", 2)))
                a.set_colors(stroke=_rgb(e.get("color")))
                a.set_opacity(float(e.get("opacity", 1)))
                a.update()
        elif t == "rect":
            page.draw_rect(_derot(page, e["rect"]), color=_rgb(e.get("color")), width=float(e.get("width", 2)),
                           overlay=True)


def _line_runs(fonts: "_Fonts", e: dict):
    """Fonts per word, final size and width (pt) of an edited PDF line."""
    ln = e["line"]
    text = e.get("text", "").replace("\n", " ")
    size = float(e.get("size") or ln["size"])
    fam = e.get("family") or ln["family"]
    runs = fonts.runs(text, ln, fam, e.get("bold", ln["bold"]), e.get("italic", ln["italic"]))
    width = _runs_width(runs, size)
    avail = ln["bbox"][2] - ln["bbox"][0]
    if e.get("fit", False) and avail * 1.02 < width < avail * 1.35:   # keep a slightly longer edit inside the line
        size *= avail / width
        width = _runs_width(runs, size)
    return runs, size, width


def _runs_width(runs, size: float) -> float:
    if not runs:
        return 0.0
    space = runs[0][2].text_length(" ", fontsize=size)
    return sum(f.text_length(w, fontsize=size) for w, _, f in runs) + space * (len(runs) - 1)


def _put_runs(page: pymupdf.Page, fonts: "_Fonts", pt_display: pymupdf.Point, runs, size: float, color, rot: int) -> None:
    """Insert one line word by word (real spaces stay searchable/copyable; each word may use its own font)."""
    if not runs:
        return
    x = pt_display.x
    space = runs[0][2].text_length(" ", fontsize=size)
    for word, name, font in runs:
        if word:
            p = pymupdf.Point(x, pt_display.y) * page.derotation_matrix
            page.insert_text(p, word, fontsize=size, fontname=fonts.use(name), color=color, rotate=rot)
            x += font.text_length(word, fontsize=size)
        x += space


def _put(page: pymupdf.Page, pt_display: pymupdf.Point, text: str, size: float, name: str,
         font: pymupdf.Font, color, rot: int) -> None:
    """Insert one line word by word (keeps real spaces searchable/copyable in every font)."""
    x = pt_display.x
    space = font.text_length(" ", fontsize=size)
    for i, word in enumerate(text.split(" ")):
        if word:
            p = pymupdf.Point(x, pt_display.y) * page.derotation_matrix
            page.insert_text(p, word, fontsize=size, fontname=name, color=color, rotate=rot)
            x += font.text_length(word, fontsize=size)
        x += space


def _wrap(text: str, font: pymupdf.Font, size: float, width: float) -> list[str]:
    if not text:
        return [""]
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        trial = (cur + " " + w) if cur else w
        if font.text_length(trial, fontsize=size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _fix_tounicode(doc: pymupdf.Document) -> None:
    """System fonts share one glyph for space/no-break-space and hyphen/soft-hyphen; MuPDF maps
    them to the rare variant. Point them back at the normal characters so copy/search work."""
    done = set()
    for page in doc:
        for f in page.get_fonts(full=True):
            xref, name = f[0], f[4]
            if xref in done or not (name.startswith("sys") or name.startswith("orig")):
                continue
            done.add(xref)
            key = doc.xref_get_key(xref, "ToUnicode")
            if key[0] != "xref":
                continue
            tx = int(key[1].split()[0])
            data = doc.xref_stream(tx)
            new = data
            for bad, good in ((b"<00AD>", b"<002D>"), (b"<00ad>", b"<002d>"), (b"<00A0>", b"<0020>"), (b"<00a0>", b"<0020>")):
                new = new.replace(bad, good)
            if new != data:
                doc.update_stream(tx, new)


def build(sid: str, spec: dict, out_path: Path) -> Path:
    """spec = {"pages": [{"src","index","rotate","edits"} | {"blank":{"w","h"},"rotate","edits"}]}"""
    out = pymupdf.open()
    for p in spec.get("pages", []):
        if p.get("blank"):
            page = out.new_page(width=p["blank"]["w"], height=p["blank"]["h"])
            src_doc = None
        else:
            src_doc = _doc(sid, p["src"])
            out.insert_pdf(src_doc, from_page=p["index"], to_page=p["index"], annots=True, widgets=True)
            page = out[-1]
        edits = p.get("edits") or []
        scan = [e for e in edits if e["type"] == "text_edit" and e["line"].get("source") == "ocr"]
        if scan and src_doc is not None:
            m = _scan_model(sid, p["src"], p["index"])
            with m.lock:
                try:
                    for e in scan:
                        ln = _scan_line(m, e["line"])
                        if ln is not None:
                            m.edit(ln, e.get("text", ""), _scan_style(e, e["line"]))
                    m.cv.write_back(page)
                finally:
                    m.cv.revert()
            edits = [e for e in edits if e not in scan]
        if edits:
            _apply(page, edits, src_doc)
        if p.get("rotate"):
            page.set_rotation((page.rotation + int(p["rotate"])) % 360)
    if not len(out):
        raise EditError("The document has no pages")
    _fix_tounicode(out)
    # garbage=4 also merges identical streams: pages are copied one by one, and without it every page
    # carries its own copy of the shared fonts/images (a 0.5 MB deck came out at 7 MB)
    out.save(out_path, garbage=4, deflate=True, clean=True)
    return out_path
