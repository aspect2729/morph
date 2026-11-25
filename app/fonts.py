"""Find TrueType fonts on macOS or Linux (Docker image ships Liberation + DejaVu).

Liberation Sans/Serif/Mono are metric-compatible with Arial/Times New Roman/Courier New,
so layouts match on every platform.
"""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

def _libreoffice_fonts() -> list[str]:
    """LibreOffice ships Liberation, Carlito, Caladea, DejaVu... (the desktop app bundles LibreOffice)."""
    out = []
    for so in filter(None, [os.environ.get("MORPH_SOFFICE"), "/Applications/LibreOffice.app/Contents/MacOS/soffice"]):
        prog = Path(so).parent
        out += [str(prog.parent / "Resources" / "fonts" / "truetype"),       # macOS .app
                str(prog.parent / "share" / "fonts" / "truetype")]           # Windows / Linux
    return out


DIRS = [Path(p) for p in (
    os.environ.get("MORPH_FONT_DIR", ""), *_libreoffice_fonts(),
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts") if os.name == "nt" else "",
    "/System/Library/Fonts/Supplemental", "/Library/Fonts", str(Path.home() / "Library/Fonts"),
    "/usr/share/fonts/truetype/liberation", "/usr/share/fonts/truetype/liberation2",
    "/usr/share/fonts/liberation", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
    "/usr/share/fonts/TTF", "/usr/share/fonts/truetype/msttcorefonts",
) if p]

NAMES = {
    ("sans", False, False): ["Arial.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
    ("sans", True, False): ["Arial Bold.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    ("sans", False, True): ["Arial Italic.ttf", "LiberationSans-Italic.ttf", "DejaVuSans-Oblique.ttf"],
    ("sans", True, True): ["Arial Bold Italic.ttf", "LiberationSans-BoldItalic.ttf", "DejaVuSans-BoldOblique.ttf"],
    ("serif", False, False): ["Times New Roman.ttf", "LiberationSerif-Regular.ttf", "DejaVuSerif.ttf"],
    ("serif", True, False): ["Times New Roman Bold.ttf", "LiberationSerif-Bold.ttf", "DejaVuSerif-Bold.ttf"],
    ("serif", False, True): ["Times New Roman Italic.ttf", "LiberationSerif-Italic.ttf", "DejaVuSerif-Italic.ttf"],
    ("serif", True, True): ["Times New Roman Bold Italic.ttf", "LiberationSerif-BoldItalic.ttf", "DejaVuSerif-BoldItalic.ttf"],
    ("mono", False, False): ["Courier New.ttf", "LiberationMono-Regular.ttf", "DejaVuSansMono.ttf"],
    ("mono", True, False): ["Courier New Bold.ttf", "LiberationMono-Bold.ttf", "DejaVuSansMono-Bold.ttf"],
    ("mono", False, True): ["Courier New Italic.ttf", "LiberationMono-Italic.ttf", "DejaVuSansMono-Oblique.ttf"],
    ("mono", True, True): ["Courier New Bold Italic.ttf", "LiberationMono-BoldItalic.ttf", "DejaVuSansMono-BoldOblique.ttf"],
}


# Microsoft/Adobe names -> metric-compatible free fonts installed in the image (same widths = same layout)
ALIASES = {
    "calibri": "carlito", "cambria": "caladea", "arial": "liberation sans", "arialmt": "liberation sans",
    "helvetica": "liberation sans", "helveticaneue": "liberation sans", "timesnewroman": "liberation serif",
    "timesnewromanpsmt": "liberation serif", "times": "liberation serif", "couriernew": "liberation mono",
    "couriernewpsmt": "liberation mono", "courier": "liberation mono",
}
_STYLE = re.compile(r"(bold|semibold|demibold|black|heavy|extrabold|italic|oblique|regular|medium|light|thin|psmt|mt)$")


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def family_of(pdf_font: str) -> str:
    """'ABCDEF+Carlito-BoldItalic' -> 'carlito'; 'Arial,Bold' -> 'arial'; 'TimesNewRomanPSMT' -> 'timesnewroman'."""
    n = re.sub(r"^[A-Z]{6}\+", "", pdf_font or "")
    n = re.split(r"[-,]", n, 1)[0]
    k = _key(n)
    for _ in range(3):                      # peel CamelCase style suffixes: "ArialBold" -> "arial"
        k2 = _STYLE.sub("", k)
        if k2 == k or not k2:
            break
        k = k2
    return k


@lru_cache(maxsize=None)
def _installed() -> dict[str, list[Path]]:
    """family key -> font files, from fontconfig (Linux) or a scan of the font folders (macOS)."""
    out: dict[str, list[Path]] = {}
    try:
        r = subprocess.run(["fc-list", "--format", "%{family[0]}\t%{file}\n"], capture_output=True, text=True, timeout=20)
        for line in r.stdout.splitlines():
            fam, _, f = line.partition("\t")
            if _key(fam) and f.lower().endswith((".ttf", ".otf")):
                out.setdefault(_key(fam), []).append(Path(f))
    except (OSError, subprocess.SubprocessError):
        pass
    # LibreOffice ships Carlito/Caladea etc. inside its own folder (the desktop app bundles it)
    for d in map(Path, _libreoffice_fonts()):
        if d.is_dir():
            for f in list(d.glob("*.ttf")) + list(d.glob("*.otf")):
                out.setdefault(family_of(f.stem), []).append(f)
    if len(out) < 5:
        for d in DIRS + [Path("/System/Library/Fonts"), Path("/usr/share/fonts")]:
            if d.is_dir():
                for f in list(d.glob("*.tt[fc]")) + list(d.glob("*.otf")) + list(d.glob("*/*.ttf")):
                    out.setdefault(family_of(f.stem), []).append(f)
    return out


@lru_cache(maxsize=None)
def by_name(pdf_font: str, bold: bool, italic: bool) -> Path | None:
    """The full font file for a PDF font name (or its metric-compatible stand-in), in the right style.
    None when nothing matching is installed."""
    fam = family_of(pdf_font)
    if not fam:
        return None
    inst = _installed()
    files = None
    for k in (fam, fam[:-2] if fam.endswith("ps") else None):
        if k and (inst.get(k) or (k in ALIASES and inst.get(_key(ALIASES[k])))):
            files = inst.get(k) or inst.get(_key(ALIASES[k]))
            break
    if not files:
        return None
    raw = re.sub(r"^[A-Z]{6}\+", "", pdf_font).lower()
    want_w = next((w for w in ("semibold", "demibold", "extrabold", "black", "heavy", "medium", "light", "thin")
                   if w in raw), "bold" if bold else "regular")

    def score(f: Path) -> int:
        s = f.stem.lower()
        is_bold = any(w in s for w in ("bold", "black", "heavy"))
        is_it = "italic" in s or "oblique" in s
        sc = (is_it == bool(italic)) * 4 + (is_bold == bool(bold)) * 2
        if want_w in s or (want_w == "regular" and not any(w in s for w in ("bold", "light", "thin", "medium", "black", "semi"))):
            sc += 1
        return sc
    return max(files, key=score)


@lru_cache(maxsize=None)
def path(family: str = "sans", bold: bool = False, italic: bool = False) -> Path | None:
    key = (family if family in ("sans", "serif", "mono") else "sans", bool(bold), bool(italic))
    for name in NAMES[key]:
        for d in DIRS:
            p = d / name
            if p.exists():
                return p
    return None
