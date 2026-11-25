"""Seamless editing of scanned / photographed text.

Instead of laying new text *over* a scan, Morph rewrites the scan itself:

  * OCR (Tesseract, hOCR with per-character boxes) finds every word, character and the
    baseline of each (possibly tilted) line.
  * Each character is cut out of the scan as a glyph sample (its real pixels, blur and ink).
  * When a line is edited, the unchanged characters stay pixel-identical; only characters
    that are new or have to move are removed (the paper is rebuilt by inpainting) and
    redrawn using glyphs cloned from the same line or elsewhere on the page — so the new
    text is literally made of the document's own letters. A matching font is only used for
    characters that never appear on the page.
  * The result is written back into the page's scan image itself (no extra layer), or, when
    the page has no single scan image, as a seamless patch.
"""
from __future__ import annotations

import difflib
import html.parser
import io
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import fonts

OCR_DPI = 300
SYS = Path("/System/Library/Fonts/Supplemental")
FONTS = {
    ("sans", False, False): "Arial.ttf", ("sans", True, False): "Arial Bold.ttf",
    ("sans", False, True): "Arial Italic.ttf", ("sans", True, True): "Arial Bold Italic.ttf",
    ("serif", False, False): "Times New Roman.ttf", ("serif", True, False): "Times New Roman Bold.ttf",
    ("serif", False, True): "Times New Roman Italic.ttf", ("serif", True, True): "Times New Roman Bold Italic.ttf",
    ("mono", False, False): "Courier New.ttf", ("mono", True, False): "Courier New Bold.ttf",
    ("mono", False, True): "Courier New Italic.ttf", ("mono", True, True): "Courier New Bold Italic.ttf",
}
CAP = {"sans": 0.716, "serif": 0.662, "mono": 0.571}       # cap height / em of the fallback fonts
CAPLIKE = set("0123456789ABCDEFGHIKLMNOPRSTUVWXYZbdfhklt")  # glyphs whose height ≈ cap height
XLIKE = set("acemnorsuvwxz")
DESC = set("gjpqy,;QJ")


# =============================================================================== OCR (hOCR)

class _HOCR(html.parser.HTMLParser):
    LINE = {"ocr_line", "ocr_caption", "ocr_header", "ocr_textfloat"}

    def __init__(self):
        super().__init__()
        self.lines, self.line, self.word, self.char = [], None, None, None
        self.stack = []

    @staticmethod
    def _title(attrs):
        t = dict(attrs).get("title", "")
        out = {}
        for part in t.split(";"):
            p = part.strip().split()
            if p:
                out[p[0]] = p[1:]
        return out

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "")
        self.stack.append(cls)
        t = self._title(attrs)
        if cls in self.LINE:
            bb = [int(v) for v in t.get("bbox", [0, 0, 0, 0])]
            bl = [float(v) for v in t.get("baseline", [0, 0])]
            self.line = {"bbox": bb, "baseline": bl, "words": []}
            self.lines.append(self.line)
        elif cls == "ocrx_word" and self.line is not None:
            bb = [int(v) for v in t.get("bbox", [0, 0, 0, 0])]
            conf = float(t.get("x_wconf", [0])[0])
            self.word = {"bbox": bb, "conf": conf, "chars": []}
            self.line["words"].append(self.word)
        elif cls == "ocrx_cinfo" and self.word is not None:
            bb = [int(v) for v in t.get("x_bboxes", [0, 0, 0, 0])]
            self.char = {"bbox": bb, "c": ""}
            self.word["chars"].append(self.char)

    def handle_endtag(self, tag):
        cls = self.stack.pop() if self.stack else ""
        if cls == "ocrx_cinfo":
            self.char = None
        elif cls == "ocrx_word":
            self.word = None

    def handle_data(self, data):
        if self.char is not None:
            self.char["c"] += data


def _tess(img: Path, psm: str, lang: str, tesseract: str) -> list[dict]:
    out = subprocess.run([tesseract, str(img), "stdout", "-l", lang, "--psm", psm,
                          "-c", "hocr_char_boxes=1", "hocr"], capture_output=True, text=True, timeout=240).stdout
    p = _HOCR()
    p.feed(out)
    lines = []
    for ln in p.lines:
        words = []
        for w in ln["words"]:
            w["chars"] = [c for c in w["chars"] if c["c"] and not c["c"].isspace()]
            text = "".join(c["c"] for c in w["chars"])
            if not text or w["conf"] < 8 or (w["conf"] < 40 and not any(ch.isalnum() for ch in text)):
                continue
            w["text"] = text
            words.append(w)
        if words:
            ln["words"] = words
            lines.append(ln)
    return lines


def ocr(img: Path, tesseract: str) -> list[dict]:
    """Two passes: layout (psm 3) + sparse text (psm 11) to catch isolated table cells."""
    lang = os.environ.get("MORPH_OCR_LANG", "eng")
    with ThreadPoolExecutor(2) as ex:
        a, b = ex.submit(_tess, img, "3", lang, tesseract), ex.submit(_tess, img, "11", lang, tesseract)
        main, sparse = a.result(), b.result()
    have = [w["bbox"] for ln in main for w in ln["words"]]

    def overlaps(bb):
        for o in have:
            ix = min(bb[2], o[2]) - max(bb[0], o[0]); iy = min(bb[3], o[3]) - max(bb[1], o[1])
            if ix > 0 and iy > 0 and ix * iy > 0.3 * max(1, (bb[2] - bb[0]) * (bb[3] - bb[1])):
                return True
        return False
    for ln in sparse:
        ws = [w for w in ln["words"] if not overlaps(w["bbox"])]
        if ws:
            ln["words"] = ws
            main.append(ln)
            have += [w["bbox"] for w in ws]
    return main


def segments(lines: list[dict]) -> list[dict]:
    """Split OCR lines at table-column gaps; each segment is one editable line."""
    segs = []
    for ln in lines:
        words = sorted(ln["words"], key=lambda w: w["bbox"][0])
        mh = float(np.median([w["bbox"][3] - w["bbox"][1] for w in words]))
        cur = [words[0]]
        for w in words[1:]:
            prev = cur[-1]
            gap = w["bbox"][0] - prev["bbox"][2]
            jump = abs((w["bbox"][1] + w["bbox"][3]) / 2 - (prev["bbox"][1] + prev["bbox"][3]) / 2)
            if gap > max(mh * 1.6, 25) or jump > mh * 0.7:
                segs.append({"line": ln, "words": cur}); cur = [w]
            else:
                cur.append(w)
        segs.append({"line": ln, "words": cur})
    return segs


# =============================================================================== canvas

class Canvas:
    """Pixels we edit: the page's scan image (native resolution) or a page raster."""

    def __init__(self, page: pymupdf.Page):
        self.page = page
        self.kind = "raster"
        self.xref = 0
        info = None
        if page.rotation == 0:
            best = 0
            for i in page.get_image_info(xrefs=True):
                a, b, c, d = i["transform"][:4]
                r = pymupdf.Rect(i["bbox"])
                area = r.get_area()
                if i["xref"] and abs(b) < 1e-3 and abs(c) < 1e-3 and a > 0 and d > 0 and area > best \
                        and area > 0.5 * page.rect.get_area():
                    best, info = area, i
        if info:
            ext = page.parent.extract_image(info["xref"])
            if ext and not ext.get("smask"):
                im = Image.open(io.BytesIO(ext["image"]))
                self.mode = im.mode
                self.arr = np.array(im.convert("RGB"))
                self.kind, self.xref, self.ext = "image", info["xref"], ext["ext"]
                self.rect = pymupdf.Rect(info["bbox"])
                h, w = self.arr.shape[:2]
                self.sx, self.sy = w / self.rect.width, h / self.rect.height
                self.ox, self.oy = self.rect.x0, self.rect.y0
        if self.kind == "raster":
            pix = page.get_pixmap(dpi=OCR_DPI, alpha=False, colorspace=pymupdf.csRGB)
            self.arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
            self.sx = self.sy = OCR_DPI / 72
            self.ox = self.oy = 0
        self.orig = self.arr.copy()
        self.gray = cv2.cvtColor(self.orig, cv2.COLOR_RGB2GRAY).astype(np.float32)
        self.dirty: list[tuple[int, int, int, int]] = []

    # display-space points  <->  canvas pixels   (rotation 0 for image canvases)
    def px(self, x, y):
        return (x - self.ox) * self.sx, (y - self.oy) * self.sy

    def pt(self, X, Y):
        return X / self.sx + self.ox, Y / self.sy + self.oy

    def write_back(self, page: pymupdf.Page) -> None:
        """Put the edited pixels into `page` (the page being saved)."""
        if not self.dirty:
            return
        if self.kind == "image":
            xref = 0
            for i in page.get_image_info(xrefs=True):
                r = pymupdf.Rect(i["bbox"])
                if i["xref"] and abs(r.x0 - self.rect.x0) < 1 and abs(r.y0 - self.rect.y0) < 1 \
                        and abs(r.width - self.rect.width) < 1 and abs(r.height - self.rect.height) < 1:
                    xref = i["xref"]
                    break
            if xref:
                im = Image.fromarray(self.arr)
                if self.mode == "L":
                    im = im.convert("L")
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=94, subsampling=0, optimize=True)
                page.replace_image(xref, stream=buf.getvalue())
                return
        # no matching scan image: drop in a seamless patch of the changed area
        x0 = max(0, min(d[0] for d in self.dirty) - 4); y0 = max(0, min(d[1] for d in self.dirty) - 4)
        x1 = min(self.arr.shape[1], max(d[2] for d in self.dirty) + 4); y1 = min(self.arr.shape[0], max(d[3] for d in self.dirty) + 4)
        buf = io.BytesIO()
        Image.fromarray(self.arr[y0:y1, x0:x1]).save(buf, "PNG")
        r = pymupdf.Rect(*self.pt(x0, y0), *self.pt(x1, y1)) * page.derotation_matrix
        page.insert_image(r, stream=buf.getvalue(), rotate=(360 - page.rotation) % 360)

    def revert(self) -> None:
        for x0, y0, x1, y1 in self.dirty:
            self.arr[y0:y1, x0:x1] = self.orig[y0:y1, x0:x1]
        self.dirty.clear()


# =============================================================================== glyphs

class Glyph:
    __slots__ = ("c", "box", "bot", "bg", "ink", "cap", "src", "clean", "rgb", "feat")

    def __init__(self, c, box, bot, bg, ink, cap, src, clean=False):
        self.c, self.box, self.bot, self.bg, self.ink, self.cap, self.src = c, box, bot, bg, ink, cap, src
        self.clean = clean      # True when the glyph is its own separate blob of ink (safe to copy)
        self.feat = None


class Line:
    """One editable segment in canvas pixels."""

    def __init__(self, cv: Canvas, seg: dict, k: float):
        self.cv = cv
        ln = seg["line"]
        # hOCR baseline: y = y1 + m*(x - x0) + c (OCR raster pixels) → canvas pixels
        lx0, ly1 = ln["bbox"][0], ln["bbox"][3]
        m, c = ln["baseline"]
        X0, Y0 = cv.px(lx0 * k, (ly1 + c) * k)
        X1, Y1 = cv.px((lx0 + 1000) * k, (ly1 + c + m * 1000) * k)
        self.m = (Y1 - Y0) / (X1 - X0)
        self.b0 = (X0, Y0)
        self.words = []
        for w in seg["words"]:
            self.words.append(self._word(w, k))
        self.chars = [g for w in self.words for g in w]
        caps = [g.box[3] - g.box[1] for g in self.chars if g.c in CAPLIKE]
        xs = [g.box[3] - g.box[1] for g in self.chars if g.c in XLIKE]
        self.cap = float(np.median(caps)) if caps else (float(np.median(xs)) / 0.72 if xs else
                                                         float(np.median([g.box[3] - g.box[1] for g in self.chars])))
        for g in self.chars:
            g.cap = self.cap
        self._fit_baseline()
        inks = [g.rgb for g in self.chars]
        self.ink = np.median(np.array(inks), axis=0) if inks else np.array([30, 30, 30])
        # spacing statistics
        gaps = [b.box[0] - a.box[2] for w in self.words for a, b in zip(w, w[1:])]
        self.gap = float(np.median(gaps)) if gaps else self.cap * 0.08
        wg = [self.words[i + 1][0].box[0] - self.words[i][-1].box[2] for i in range(len(self.words) - 1)]
        self.space = float(np.median(wg)) if wg else self.cap * 0.38
        self.text = " ".join("".join(g.c for g in w) for w in self.words)

    def _fit_baseline(self):
        """Baseline = bottoms of letters that sit on it. Fitted locally so it follows tilt *and* paper curl."""
        on = [g for g in self.chars if g.c not in DESC and g.c not in "-'\"^~*=+_°`,.:;"]
        self._bx = self._by = None
        if len(on) >= 2:
            xs = np.array([(g.box[0] + g.box[2]) / 2 for g in on], float)
            ys = np.array([g.box[3] for g in on], float)
            o = np.argsort(xs)
            xs, ys = xs[o], ys[o]
            m, c = (np.polyfit(xs, ys, 1) if np.ptp(xs) > 5 else (0.0, float(np.median(ys))))
            if len(xs) >= 5:
                keep = np.ones(len(xs), bool)
                for i in range(len(xs)):
                    nb = [j for j in range(max(0, i - 3), min(len(xs), i + 4)) if j != i]
                    local = np.median(ys[nb] + m * (xs[i] - xs[nb]))
                    keep[i] = abs(ys[i] - local) <= self.cap * 0.22 + 2
                if keep.sum() >= 2:
                    xs, ys = xs[keep], ys[keep]
                    if np.ptp(xs) > 5:
                        m, c = np.polyfit(xs, ys, 1)
            if abs(m) < 0.2:
                self.m, self.b0 = float(m), (0.0, float(c))
            self._bx, self._by = xs, ys
        for g in self.chars:
            g.bot = g.box[3] - self.baseline((g.box[0] + g.box[2]) / 2)

    def _lin(self, x: float) -> float:
        return self.b0[1] + self.m * (x - self.b0[0])

    def baseline(self, x: float) -> float:
        bx = getattr(self, "_bx", None)
        if bx is None or len(bx) < 3:
            return self._lin(x)
        d = np.abs(bx - x)
        idx = np.argsort(d)[:7]
        w = 1.0 / (1.0 + d[idx] / max(1.0, self.cap))
        xs, ys = bx[idx], self._by[idx]
        if np.ptp(xs) > self.cap:
            A = np.stack([xs - x, np.ones_like(xs)], 1) * w[:, None]
            sol, *_ = np.linalg.lstsq(A, ys * w, rcond=None)
            slope = float(np.clip(sol[0], self.m - 0.08, self.m + 0.08))
            # intercept at x given the (clipped) slope
            return float((w * (ys - slope * (xs - x))).sum() / w.sum())
        return float((w * (ys - self.m * (xs - x))).sum() / w.sum())

    @staticmethod
    def _cut(mask, chars, centers, X0, Y0):
        return _cut_impl(mask, len(chars), centers, X0, Y0)

    def _word(self, w: dict, k: float) -> list[Glyph]:
        cv = self.cv
        x0, y0 = cv.px(w["bbox"][0] * k, w["bbox"][1] * k)
        x1, y1 = cv.px(w["bbox"][2] * k, w["bbox"][3] * k)
        pad = max(2, int((y1 - y0) * 0.12))
        X0, Y0 = max(0, int(x0) - pad), max(0, int(y0) - pad)
        X1, Y1 = min(cv.gray.shape[1], int(x1) + pad + 1), min(cv.gray.shape[0], int(y1) + pad + 1)
        crop = cv.gray[Y0:Y1, X0:X1]
        rgb = cv.orig[Y0:Y1, X0:X1]
        bgL = float(np.percentile(crop, 90))
        inkL = float(np.percentile(crop, 3))
        thr = bgL - max(18.0, (bgL - inkL) * 0.38)
        mask = (crop < thr).astype(np.uint8)
        # ignore long straight structures (table rules) touching the word box
        n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
        comps = []
        H = Y1 - Y0
        for i in range(1, n):
            x, y, ww, hh, area = st[i]
            if area < 3 or ww > (X1 - X0) * 0.9 or hh > H * 0.98 and ww < 3 or (ww > H * 1.6 and hh < 4):
                continue
            comps.append((x + X0, y + Y0, x + ww + X0, y + hh + Y0, cen[i][0] + X0))
        chars = w["chars"]
        centers = []
        for ch in chars:
            a, _ = cv.px(ch["bbox"][0] * k, 0)
            b, _ = cv.px(ch["bbox"][2] * k, 0)
            centers.append((a + b) / 2)
        # spread centres evenly if Tesseract's char boxes collapsed
        if len(centers) > 1 and (max(centers) - min(centers)) < (x1 - x0) * 0.3:
            centers = list(np.linspace(x0 + (x1 - x0) / (2 * len(chars)), x1 - (x1 - x0) / (2 * len(chars)), len(chars)))
        groups = [[] for _ in chars]
        for cpt in comps:
            j = int(np.argmin([abs(cpt[4] - c) for c in centers]))
            groups[j].append(cpt)
        # if component count matches char count, assign in x order (most reliable)
        merged = sorted(comps, key=lambda c: c[4])
        merged = _merge_vertical(merged)
        clean = len(merged) == len(chars)
        if clean:
            groups = [[c] for c in merged]
        if len(merged) != len(chars) and len(chars) > 1:
            groups = self._cut(mask, chars, centers, X0, Y0)
        glyphs = []
        inkpix = rgb[mask.astype(bool)]
        ink = inkpix[np.argsort(inkpix.sum(axis=1))[: max(1, len(inkpix) // 4)]].mean(axis=0) if len(inkpix) else np.array([30, 30, 30])
        for ch, grp, cx in zip(chars, groups, centers):
            if grp:
                gx0 = min(g[0] for g in grp); gy0 = min(g[1] for g in grp)
                gx1 = max(g[2] for g in grp); gy1 = max(g[3] for g in grp)
            else:
                gx0, _ = cv.px(ch["bbox"][0] * k, 0); gx1, _ = cv.px(ch["bbox"][2] * k, 0)
                gy0, gy1 = y0, y1
            box = (int(gx0), int(gy0), int(np.ceil(gx1)), int(np.ceil(gy1)))
            bot = box[3] - self.baseline((box[0] + box[2]) / 2)
            gl = Glyph(ch["c"], box, bot, bgL, inkL, 0, self, clean and bool(grp))
            gl.rgb = ink
            glyphs.append(gl)
        return glyphs


def _cut_impl(mask, n, centers, X0, Y0):
    """Split a word into n glyphs at the ink-poorest columns near the expected boundaries."""
    cols = mask.sum(axis=0).astype(float)
    W = len(cols)
    inked = np.nonzero(cols)[0]
    if not len(inked):
        return [[] for _ in range(n)]
    left, right = int(inked[0]), int(inked[-1]) + 1
    cx = [c - X0 for c in centers]
    # if the OCR char centres don't span the ink, spread them evenly across it
    if cx[0] < left - 3 or cx[-1] > right + 3 or any(b <= a for a, b in zip(cx, cx[1:])):
        cx = list(np.linspace(left + (right - left) / (2 * n), right - (right - left) / (2 * n), n))
    bounds = [left]
    for i in range(n - 1):
        mid = (cx[i] + cx[i + 1]) / 2
        half = max(2.0, (cx[i + 1] - cx[i]) * 0.45)
        lo, hi = int(max(bounds[-1] + 1, mid - half)), int(min(right - 1, mid + half))
        if hi <= lo:
            cut = int(min(max(mid, bounds[-1] + 1), right - 1))
        else:
            seg = cols[lo:hi + 1]
            cands = np.nonzero(seg == seg.min())[0] + lo
            cut = int(cands[np.argmin(np.abs(cands - mid))])
        bounds.append(cut)
    bounds.append(right)
    groups = []
    for i in range(n):
        a, b = bounds[i], max(bounds[i] + 1, bounds[i + 1])
        rows = np.nonzero(mask[:, a:b].any(axis=1))[0]
        if not len(rows):
            groups.append([]); continue
        sub = np.nonzero(mask[rows[0]:rows[-1] + 1, a:b].any(axis=0))[0]
        x0, x1 = a + int(sub[0]), a + int(sub[-1]) + 1
        groups.append([(x0 + X0, int(rows[0]) + Y0, x1 + X0, int(rows[-1]) + 1 + Y0, (x0 + x1) / 2 + X0)])
    return groups


def _merge_vertical(comps):
    """Merge components that share an x-range (i/j dots, colons, accents)."""
    out = []
    for c in comps:
        if out:
            p = out[-1]
            ov = min(p[2], c[2]) - max(p[0], c[0])
            if ov > 0.5 * min(p[2] - p[0], c[2] - c[0]):
                out[-1] = (min(p[0], c[0]), min(p[1], c[1]), max(p[2], c[2]), max(p[3], c[3]), (p[4] + c[4]) / 2)
                continue
        out.append(c)
    return out


# =============================================================================== page model

class PageModel:
    def __init__(self, page: pymupdf.Page, ocr_lines: list[dict]):
        self.cv = Canvas(page)
        k = 72 / OCR_DPI
        self.lines: list[Line] = []
        for seg in segments(ocr_lines):
            try:
                self.lines.append(Line(self.cv, seg, k))
            except Exception:
                continue
        self.library: dict[str, list[Glyph]] = {}
        for ln in self.lines:
            for g in ln.chars:
                if g.box[2] - g.box[0] >= 2 and g.box[3] - g.box[1] >= 2:
                    self.library.setdefault(g.c, []).append(g)
        for ln in self.lines:
            self._analyse(ln)
        self._weights()

    def _weights(self):
        # uncertain lines follow the page's majority font (pages rarely mix body fonts)
        sure = [l.family for l in self.lines if getattr(l, "fmargin", 0) >= 0.04 and len(l.chars) >= 6]
        if sure:
            major = max(set(sure), key=sure.count)
            for l in self.lines:
                if getattr(l, "fmargin", 0) < 0.04:
                    l.family = major
        """Bold = clearly heavier strokes than the page's normal text of similar size (scan blur
        thickens everything, so absolute stroke width can't be trusted)."""
        for fam in ("sans", "serif"):
            group = [l for l in self.lines if l.family == fam and len(l.chars) >= 4]
            if len(group) < 3:
                continue
            base = float(np.median([l.stroke for l in group]))
            for l in group:
                l.bold = l.stroke > base * 1.28

    # ------------------------------------------------------------------ style analysis
    def _alpha(self, g: Glyph) -> np.ndarray:
        x0, y0, x1, y1 = g.box
        patch = self.cv.gray[y0:y1, x0:x1]
        return np.clip((g.bg - patch) / max(10.0, g.bg - g.ink), 0, 1)

    def _feat(self, g: Glyph) -> np.ndarray:
        """Size-normalised shape of a glyph (for comparing samples of the same letter)."""
        if g.feat is None:
            a = self._alpha(g)
            h, w = a.shape
            side = max(h, w, 1)
            sq = np.zeros((side, side), np.float32)
            sq[(side - h) // 2:(side - h) // 2 + h, (side - w) // 2:(side - w) // 2 + w] = a
            g.feat = np.concatenate([cv2.resize(sq, (16, 16), interpolation=cv2.INTER_AREA).ravel(),
                                     [w / max(1, h)]]).astype(np.float32)
        return g.feat

    def _analyse(self, ln: Line) -> None:
        """Stroke weight and the closest font (sans/serif, regular/bold) for a line."""
        runs = []
        for g in ln.chars[:40]:
            if not g.clean:
                continue
            m = self._alpha(g) > 0.5
            for row in m[:: max(1, m.shape[0] // 8)]:
                d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
                st, en = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
                runs += list(en - st)
        ln.stroke = (float(np.median(runs)) / max(1.0, ln.cap)) if runs else 0.12
        ln.yc = float(np.median([(g.box[1] + g.box[3]) / 2 for g in ln.chars]))
        score = {}
        probes = [g for g in ln.chars if g.c.isalnum() and g.c not in "lI1|" and
                  0.35 < (g.box[2] - g.box[0]) / max(1, g.box[3] - g.box[1]) < 1.6][:16]
        for fam in ("sans", "serif"):
            for bold in (False, True):
                vals = [_match(self._alpha(g), g.c, fam, bold) for g in probes]
                vals = [v for v in vals if v is not None]
                if vals:
                    score[(fam, bold)] = float(np.mean(vals))
        fam_score = {f: max(score.get((f, False), -1), score.get((f, True), -1)) for f in ("sans", "serif")}
        ln.family = max(fam_score, key=fam_score.get) if score else "sans"
        ln.fmargin = abs(fam_score["serif"] - fam_score["sans"]) if score else 0.0
        ln.fit = fam_score.get(ln.family, 0.0) if score else 0.0
        ln.bold = False                                   # set page-wide in _weights()

    def find(self, bbox_pt) -> Line | None:
        """Line whose box matches a client line bbox (display points)."""
        bx = [*self.cv.px(bbox_pt[0], bbox_pt[1]), *self.cv.px(bbox_pt[2], bbox_pt[3])]
        best, score = None, 0
        for ln in self.lines:
            xs = [g.box for g in ln.chars]
            lb = (min(b[0] for b in xs), min(b[1] for b in xs), max(b[2] for b in xs), max(b[3] for b in xs))
            ix = min(lb[2], bx[2]) - max(lb[0], bx[0]); iy = min(lb[3], bx[3]) - max(lb[1], bx[1])
            if ix > 0 and iy > 0:
                s = ix * iy / max(1, (lb[2] - lb[0]) * (lb[3] - lb[1]) + (bx[2] - bx[0]) * (bx[3] - bx[1]) - ix * iy)
                if s > score:
                    best, score = ln, s
        return best if score > 0.3 else None

    # ------------------------------------------------------------------ editing
    def edit(self, ln: Line, new: str, style: dict | None = None) -> tuple[int, int, int, int]:
        """Rewrite one line inside the canvas. Returns the changed pixel box (or zeros)."""
        cv = self.cv
        seq, gl = [], []                      # old text with explicit spaces; glyph per index
        for wi, w in enumerate(ln.words):
            if wi:
                seq.append(" "); gl.append(None)
            for g in w:
                seq.append(g.c); gl.append(g)
        old = "".join(seq)
        if new == old and not style:
            return (0, 0, 0, 0)
        restyle = bool(style)
        fam = (style or {}).get("family", "sans")
        cap = ln.cap if not (style and style.get("size")) else style["size"] * CAP.get(fam, 0.716) * cv.sy
        ink = ln.ink if not (style and style.get("color")) else np.array(_rgb(style["color"]))
        start_x = ln.chars[0].box[0]
        tol = max(1.5, ln.cap * 0.14)          # spacing changes smaller than this are absorbed

        fb = style or {"family": ln.family, "bold": ln.bold}     # font for letters not on the page
        lo, ln_ = len(old), len(new)
        p = 0
        if not restyle:
            while p < min(lo, ln_) and old[p] == new[p]:
                p += 1
        sfx = 0
        if not restyle:
            while sfx < min(lo, ln_) - p and old[lo - 1 - sfx] == new[ln_ - 1 - sfx]:
                sfx += 1
        # unchanged start: untouched.  changed middle: typeset in order.  unchanged end: kept or slid as a block.
        placed, erase = [], [gl[i] for i in range(p, lo - sfx) if gl[i] is not None]
        block = None
        b = p - 1
        while b >= 0 and gl[b] is None:
            b -= 1
        prev_right = gl[b].box[2] if b >= 0 else None
        pend_space = p > 0 and old[p - 1] == " "
        first_old = gl[p] if p < lo - sfx else None
        first = True
        # same-length change (e.g. a date): every new character takes its old character's place
        positional = (not restyle and lo - sfx - p == ln_ - sfx - p and
                      all((old[p + j] == " ") == (new[p + j] == " ") for j in range(ln_ - sfx - p)))
        for j, ch in enumerate(new[p:ln_ - sfx]):
            if positional and ch != " ":
                og = gl[p + j]
                src = self._source(ch, ln)
                w = self._width(ch, src, cap, fb)
                ox = og.box[0] + ((og.box[2] - og.box[0]) - w) / 2      # centre in the old slot
                if prev_right is not None:
                    ox = max(ox, prev_right + ln.gap * 0.5)
                placed.append((ch, ox, src, False))
                prev_right, first = ox + w, False
                continue
            if positional:
                continue
            if ch == " ":
                pend_space = True
                continue
            src = None if restyle else self._source(ch, ln)
            w = self._width(ch, src, cap, fb)
            if first and first_old is not None and not restyle and not pend_space:
                x = first_old.box[0]
            elif prev_right is None:
                x = start_x
            else:
                x = prev_right + (ln.space if pend_space else ln.gap)
            if prev_right is not None:
                x = max(x, prev_right + ln.gap * 0.5)
            placed.append((ch, x, src, False))
            prev_right, pend_space, first = x + w, False, False
        k = lo - sfx
        while k < lo and gl[k] is None:
            k += 1
        if k < lo:
            nxt = gl[k]
            bb = k - 1
            while bb >= 0 and gl[bb] is None:
                bb -= 1
            old_space = bb >= 0 and any(old[t] == " " for t in range(bb + 1, k))
            new_space = pend_space or any(old[t] == " " for t in range(lo - sfx, k))
            if prev_right is None:
                desired = start_x
            elif old_space == new_space and bb >= 0:
                desired = prev_right + (nxt.box[0] - gl[bb].box[2])
            else:
                desired = prev_right + (ln.space if new_space else ln.gap)
            shift = desired - nxt.box[0]
            if abs(shift) > tol or restyle:
                tail = [gl[i] for i in range(k, lo) if gl[i] is not None]
                erase += tail
                block = (tail, shift)

        if not erase and all(keep for *_, keep in placed) and block is None:
            return (0, 0, 0, 0)
        H, W = cv.arr.shape[:2]
        mask = np.zeros((H, W), np.uint8)
        grow = max(2, int(ln.cap * 0.12))
        for g in erase:
            x0, y0, x1, y1 = g.box
            mask[max(0, y0 - grow):min(H, y1 + grow), max(0, x0 - grow):min(W, x1 + grow)] = 255
        erased = {id(g) for g in erase}
        lx0, ly0 = min(g.box[0] for g in ln.chars) - 4 * ln.cap, min(g.box[1] for g in ln.chars) - 3 * ln.cap
        lx1, ly1 = max(g.box[2] for g in ln.chars) + 4 * ln.cap, max(g.box[3] for g in ln.chars) + 3 * ln.cap
        for other in self.lines:               # never touch glyphs that stay, on this or any nearby line
            for g in other.chars:
                if id(g) in erased:
                    continue
                x0, y0, x1, y1 = g.box
                if x1 < lx0 or x0 > lx1 or y1 < ly0 or y0 > ly1:
                    continue
                mask[y0:y1, x0:x1] = 0
        box = _bbox(mask)
        if box:
            self._inpaint(mask, box, ln.cap)
        changed = list(box) if box else None
        if block is not None:                  # slide the unchanged tail as one piece of the original scan
            r = self._move_block(block[0], block[1], ln)
            if r:
                changed = list(r) if changed is None else [min(changed[0], r[0]), min(changed[1], r[1]),
                                                           max(changed[2], r[2]), max(changed[3], r[3])]
        for ch, x, src, keep in placed:
            if keep:
                continue
            r = self._draw(ch, x, src, ln, cap, ink, fb)
            if r:
                changed = list(r) if changed is None else [min(changed[0], r[0]), min(changed[1], r[1]),
                                                           max(changed[2], r[2]), max(changed[3], r[3])]
        if changed:
            cv.dirty.append(tuple(changed))
            return tuple(changed)
        return (0, 0, 0, 0)

    def _source(self, ch: str, ln: Line) -> Glyph | None:
        """Best real sample of `ch` to copy: same font look and size, near this line, and shaped
        like the other samples of that letter (so OCR mistakes and broken cuts are never used)."""
        cands = [g for g in self.library.get(ch, []) if abs(np.log(ln.cap / max(1.0, g.cap))) < 0.35]
        exp = _height_ratio(ch, ln.family, ln.bold)
        if exp:
            cands = [g for g in cands if abs(np.log(((g.box[3] - g.box[1]) / max(1.0, g.cap)) / exp)) < 0.22]
        if not cands:
            return None

        def style_cost(g):
            L = g.src
            c = 3.0 * abs(np.log(ln.cap / max(1.0, g.cap)))
            c += abs(getattr(L, "stroke", 0.12) - ln.stroke) / max(ln.stroke, 1e-3) * 1.2
            c += 0.0 if (getattr(L, "family", "sans"), getattr(L, "bold", False)) == (ln.family, ln.bold) else 0.9
            c += 0.0 if L is ln else 0.1 + min(1.0, abs(getattr(L, "yc", 0) - ln.yc) / (25 * ln.cap)) * 0.3
            c += 0.0 if g.clean else 0.7
            return c

        cands.sort(key=style_cost)
        top = cands[:20]
        costs = np.array([style_cost(g) for g in top])
        if len(top) >= 3:
            F = np.stack([self._feat(g) for g in top])
            D = np.sqrt(((F[:, None, :] - F[None, :, :]) ** 2).sum(-1))
            spread = D.sum(1) / (len(top) - 1)
            med = float(np.median(spread)) + 1e-6
            ok = spread <= med * 1.5                      # drop shapes unlike the rest (mislabels, cut-offs)
            total = costs + 0.6 * spread / med
            total[~ok] = np.inf
            i = int(np.argmin(total))
            if np.isfinite(total[i]) and costs[i] < 2.4:
                return top[i]
            return None
        g = top[0]
        # a lone sample must look like the letter it claims to be
        sim = _match(self._alpha(g), ch, ln.family, ln.bold)
        if costs[0] < 2.0 and (sim is None or sim > 0.45):
            return g
        return None

    def _move_block(self, glyphs, shift, ln):
        cv = self.cv
        X0 = min(g.box[0] for g in glyphs) - 2; Y0 = min(g.box[1] for g in glyphs) - 2
        X1 = max(g.box[2] for g in glyphs) + 2; Y1 = max(g.box[3] for g in glyphs) + 2
        X0, Y0 = max(0, X0), max(0, Y0)
        X1, Y1 = min(cv.gray.shape[1], X1), min(cv.gray.shape[0], Y1)
        keep = np.zeros((Y1 - Y0, X1 - X0), np.float32)
        for g in glyphs:
            x0, y0, x1, y1 = g.box
            keep[max(0, y0 - Y0 - 1):y1 - Y0 + 1, max(0, x0 - X0 - 1):x1 - X0 + 1] = 1
        bg = float(np.median([g.bg for g in glyphs])); ink = float(np.median([g.ink for g in glyphs]))
        a = np.clip((bg - cv.gray[Y0:Y1, X0:X1]) / max(10.0, bg - ink), 0, 1) * keep
        dy = ln.m * shift
        return self._composite(a, int(round(X0 + shift)), int(round(Y0 + dy)), ln.ink)

    def _width(self, ch, src, cap, style):
        if src is not None:
            return (src.box[2] - src.box[0]) * cap / max(1, src.cap)
        f = _font(style, cap)
        b = f.getbbox(ch)
        return max(1, b[2] - b[0])

    def _inpaint(self, mask, box, cap):
        """Rebuild the paper under removed text from the surrounding *paper* only.

        Every pen stroke near the hole (other letters, table rules) is masked out first, so the
        fill is the true paper colour and texture — no ink bleeds in and nothing is brightened."""
        cv = self.cv
        x0, y0, x1, y1 = box
        m = int(cap * 1.5) + 10
        X0, Y0 = max(0, x0 - m), max(0, y0 - m)
        X1, Y1 = min(cv.arr.shape[1], x1 + m), min(cv.arr.shape[0], y1 + m)
        src = np.ascontiguousarray(cv.orig[Y0:Y1, X0:X1])
        hole = mask[Y0:Y1, X0:X1] > 0
        # find ink: anything clearly darker than the local paper level
        g = cv2.cvtColor(src, cv2.COLOR_RGB2GRAY).astype(np.float32)
        k = max(5, int(cap * 0.9)) | 1
        level = cv2.morphologyEx(g, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
        level = cv2.GaussianBlur(level, (0, 0), max(1.0, cap * 0.1))
        ink = (level - g) > max(12.0, float(np.std(g[~hole])) * 0.9 if (~hole).any() else 12.0)
        ink = cv2.dilate(ink.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        fill_mask = (hole | ink).astype(np.uint8) * 255
        filled = cv2.inpaint(src, fill_mask, max(3, int(cap * 0.15)), cv2.INPAINT_TELEA).astype(np.float32)
        # match the paper's grain, measured on clean paper pixels around the hole
        clean = src[~(hole | ink)]
        if len(clean) > 50:
            sd = np.clip(clean.astype(np.float32).std(axis=0) * 0.85, 0, 6)
            blur_sd = np.clip(cv2.GaussianBlur(src.astype(np.float32), (0, 0), 1.2)[~(hole | ink)].std(axis=0), 0, 6)
            grain = np.sqrt(np.maximum(sd ** 2 - blur_sd ** 2, 0)) + 0.5
            noise = cv2.GaussianBlur(np.random.normal(0, 1, filled.shape).astype(np.float32), (0, 0), 0.6)
            filled += noise * grain * 1.6
        out = cv.arr[Y0:Y1, X0:X1].copy()
        out[hole] = np.clip(filled[hole], 0, 255).astype(np.uint8)
        cv.arr[Y0:Y1, X0:X1] = out

    def _draw(self, ch, x, src, ln, cap, ink, style):
        cv = self.cv
        if src is not None:
            s = cap / max(1, src.cap)
            gx0, gy0, gx1, gy1 = src.box
            p = 1
            patch = cv.gray[max(0, gy0 - p):gy1 + p, max(0, gx0 - p):gx1 + p]
            a = np.clip((src.bg - patch) / max(10.0, src.bg - src.ink), 0, 1)
            # hard-limit to the glyph's own columns: no slivers of touching neighbours
            a[:, :p] *= 0.35; a[:, -p:] *= 0.35
            # keep only this glyph's pixels (drop neighbouring characters bleeding into the box)
            a = _isolate(a)
            if abs(s - 1) > 0.03:
                a = cv2.resize(a, (max(1, int(round(a.shape[1] * s))), max(1, int(round(a.shape[0] * s)))),
                               interpolation=cv2.INTER_CUBIC)
                a = np.clip(a, 0, 1)
            bot = src.bot * s
            if src.src is ln:
                pass                                                   # same line: offset is exact
            elif ch in DESC:
                bot = float(np.clip(bot, 0.05 * cap, 0.4 * cap))       # descenders hang below the line
            elif ch in "-'\"^~*=+_°`":
                bot = float(np.clip(bot, -0.85 * cap, -0.15 * cap))    # floating marks keep their height
            else:
                bot = 0.0                                              # everything else sits on the line
            top = ln.baseline(x + a.shape[1] / 2) + bot - (a.shape[0] - 2 * p * s)
            return self._composite(a, int(round(x - p * s)), int(round(top - p * s)), ink)
        # fallback: a real font, softened to the scan's blur
        f = _font(style, cap)
        asc, desc = f.getmetrics()
        b = f.getbbox(ch)
        im = Image.new("L", (b[2] - b[0] + 8, asc + desc + 8), 0)
        ImageDraw.Draw(im).text((4 - b[0], 4), ch, font=f, fill=255)
        im = im.filter(ImageFilter.GaussianBlur(max(0.35, cap * 0.025)))
        a = np.asarray(im, np.float32) / 255
        top = ln.baseline(x) - asc - 4
        return self._composite(a, int(round(x - 4)), int(round(top)), ink)

    def _composite(self, a, X, Y, ink):
        cv = self.cv
        H, W = cv.arr.shape[:2]
        h, w = a.shape
        x0, y0, x1, y1 = max(0, X), max(0, Y), min(W, X + w), min(H, Y + h)
        if x1 <= x0 or y1 <= y0:
            return None
        aa = a[y0 - Y:y1 - Y, x0 - X:x1 - X][..., None]
        dst = cv.arr[y0:y1, x0:x1].astype(np.float32)
        out = dst * (1 - aa) + np.asarray(ink, np.float32) * aa
        # never lighten: ink only darkens paper
        cv.arr[y0:y1, x0:x1] = np.minimum(dst, out).clip(0, 255).astype(np.uint8)
        return (x0, y0, x1, y1)

    def patch_png(self, box, pad=6) -> tuple[bytes, list[float]]:
        cv = self.cv
        x0, y0, x1, y1 = box
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(cv.arr.shape[1], x1 + pad), min(cv.arr.shape[0], y1 + pad)
        buf = io.BytesIO()
        Image.fromarray(cv.arr[y0:y1, x0:x1]).save(buf, "PNG")
        return buf.getvalue(), [*cv.pt(x0, y0), *cv.pt(x1, y1)]

    def reset(self):
        self.cv.arr[:] = self.cv.orig
        self.cv.dirty.clear()


def _isolate(a: np.ndarray) -> np.ndarray:
    """Keep the connected ink blobs that overlap the box centre column band; fade the rest."""
    m = (a > 0.25).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 2:
        return a
    h, w = a.shape
    keep = np.zeros_like(m)
    for i in range(1, n):
        x, y, ww, hh, area = st[i]
        if x + ww > w * 0.2 and x < w * 0.8 or area > 0.15 * m.sum():
            keep[lab == i] = 1
    keep = cv2.dilate(keep, np.ones((3, 3), np.uint8))
    return a * keep


def _bbox(mask):
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def _rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


_font_cache: dict = {}


def _font(style: dict, cap_px: float) -> ImageFont.FreeTypeFont:
    fam = style.get("family") if style.get("family") in CAP else "sans"
    size = max(4, int(round(cap_px / CAP[fam])))
    ck = (fam, bool(style.get("bold")), bool(style.get("italic")), size)
    if ck not in _font_cache:
        p = fonts.path(fam, bool(style.get("bold")), bool(style.get("italic")))
        _font_cache[ck] = ImageFont.truetype(str(p), size) if p else ImageFont.load_default()
    return _font_cache[ck]


_ref_cache: dict = {}


def _ref(ch: str, fam: str, bold: bool) -> np.ndarray | None:
    """A letter rendered in a reference font, cropped to its ink, as alpha (0..1)."""
    key = (ch, fam, bold)
    if key not in _ref_cache:
        p = fonts.path(fam, bold, False)
        if p is None:
            _ref_cache[key] = None
        else:
            f = ImageFont.truetype(str(p), 96)
            im = Image.new("L", (160, 160), 0)
            ImageDraw.Draw(im).text((20, 10), ch, font=f, fill=255)
            a = np.asarray(im, np.float32) / 255
            ys, xs = np.nonzero(a > 0.2)
            _ref_cache[key] = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(xs) else None
    return _ref_cache[key]


def _height_ratio(ch: str, fam: str, bold: bool) -> float | None:
    """Ink height of a letter relative to the capital height, in the reference font."""
    r, H = _ref(ch, fam, bold), _ref("H", fam, bold)
    if r is None or H is None:
        return None
    return r.shape[0] / H.shape[0]


def _match(a: np.ndarray, ch: str, fam: str, bold: bool) -> float | None:
    """Correlation between a scanned glyph and the reference font's version of that letter."""
    r = _ref(ch, fam, bold)
    if r is None or a.size < 16:
        return None
    ys, xs = np.nonzero(a > 0.25)
    if not len(xs):
        return None
    a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    rr = cv2.resize(r, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    a = cv2.GaussianBlur(a, (3, 3), 0.8); rr = cv2.GaussianBlur(rr, (3, 3), 0.8)
    av, rv = a - a.mean(), rr - rr.mean()
    den = float(np.sqrt((av * av).sum() * (rv * rv).sum()))
    return float((av * rv).sum() / den) if den > 0 else None


# =============================================================================== cache

_models: dict[tuple, PageModel] = {}
_lock = threading.Lock()


def put(key: tuple, m: PageModel) -> None:
    with _lock:
        m.lock = threading.Lock()
        _models[key] = m


def get(key: tuple) -> PageModel | None:
    return _models.get(key)


def drop_session(sid: str) -> None:
    with _lock:
        for k in [k for k in _models if k[0] == sid]:
            del _models[k]
